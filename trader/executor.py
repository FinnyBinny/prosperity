"""
Trade execution pipeline: validates → sizes → submits → monitors → closes.
This is the only module that actually places orders.
"""
import logging
from datetime import datetime
from typing import Optional

import config
from trader.alpaca_client import AlpacaClient
from trader.risk_manager import RiskManager, TradeSignal, ValidationResult
from trader.journal import Journal, Trade

logger = logging.getLogger(__name__)


class TradeExecutor:
    def __init__(
        self,
        alpaca: AlpacaClient,
        risk_manager: RiskManager,
        journal: Journal,
    ):
        self.alpaca = alpaca
        self.risk = risk_manager
        self.journal = journal
        self._open_trade_ids: dict[str, int] = {}  # symbol -> journal trade ID

    def execute_signal(self, signal: TradeSignal) -> Optional[dict]:
        """Full execution pipeline: validate → submit → record."""
        # Get current account state
        account = self.alpaca.get_account()
        buying_power = account["buying_power"]
        open_positions = self.alpaca.get_positions()
        current_positions_count = len(open_positions)

        # Validate
        validation = self.risk.validate_trade(
            signal, buying_power, current_positions_count, account["portfolio_value"]
        )
        if not validation.approved:
            logger.info(f"Trade REJECTED [{signal.symbol}]: {validation.reason}")
            return None

        qty = validation.final_qty
        stop_loss = validation.stop_loss
        take_profit = validation.take_profit

        logger.info(
            f"Executing {signal.action.upper()} {qty:.6f} {signal.symbol} "
            f"| confidence={signal.confidence:.2f} | SL=${stop_loss:.4f} | TP=${take_profit:.4f}"
        )

        # Submit order
        try:
            if config.ALPACA_MODE == "live" and not config.LIVE_TRADING_CONFIRMED:
                logger.warning("BLOCKED: Live trading not confirmed. Set LIVE_TRADING_CONFIRMED=yes")
                return None

            order = self.alpaca.place_market_order(signal.symbol, qty, signal.action)
            logger.info(f"Order placed: {order['id']} — status: {order['status']}")

        except Exception as e:
            logger.error(f"Order submission failed for {signal.symbol}: {e}")
            return None

        # Record in journal
        entry_price = signal.entry_price or order.get("filled_avg_price") or 0
        trade = Trade(
            symbol=signal.symbol,
            side=signal.action,
            qty=qty,
            entry_price=entry_price,
            exit_price=None,
            entry_time=datetime.now().isoformat(),
            exit_time=None,
            strategy=signal.strategy,
            rationale=signal.rationale,
            status="open",
            tags=f"{signal.catalyst},{signal.asset_class}",
            alpaca_order_id=order["id"],
        )
        trade_id = self.journal.record_trade(trade)
        self._open_trade_ids[signal.symbol] = trade_id
        logger.info(f"Trade recorded in journal: ID={trade_id}")

        return {
            "trade_id": trade_id,
            "order_id": order["id"],
            "symbol": signal.symbol,
            "qty": qty,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    def monitor_positions(self, brain=None) -> list[dict]:
        """Check all open positions against stop/target levels. Close if triggered."""
        closed = []
        open_trades = self.journal.get_open_trades()

        if not open_trades:
            return []

        for trade in open_trades:
            symbol = trade["symbol"]
            entry_price = trade["entry_price"]
            trade_id = trade["id"]

            # Get stop/target from risk manager
            asset_class = "crypto" if "/" in symbol else "stock"
            stop_loss = self.risk.get_stop_price(entry_price, asset_class)
            take_profit = self.risk.get_target_price(entry_price, asset_class)

            # Get current price
            current_price = self.alpaca.get_latest_price(symbol)
            if current_price <= 0:
                continue

            should_exit, reason = self.risk.should_exit(
                entry_price, current_price, stop_loss, take_profit
            )

            if should_exit:
                logger.info(
                    f"EXIT triggered: {symbol} @ ${current_price:.4f} "
                    f"(reason={reason}, entry=${entry_price:.4f})"
                )
                try:
                    self.alpaca.close_position(symbol)
                    pnl = (current_price - entry_price) * trade["qty"]
                    rca_summary = f"Auto-exit: {reason}"

                    # Trigger AI RCA for losses
                    if brain and pnl < 0:
                        closed_trade = {**trade, "exit_price": current_price, "pnl": pnl}
                        rca_summary = brain.perform_rca(closed_trade)

                    self.journal.close_trade(
                        trade_id,
                        exit_price=current_price,
                        exit_time=datetime.now().isoformat(),
                        status=reason,
                        rca=rca_summary,
                    )
                    closed.append({"trade_id": trade_id, "symbol": symbol, "reason": reason, "pnl": pnl})

                except Exception as e:
                    logger.error(f"Failed to close position {symbol}: {e}")
            else:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                logger.debug(
                    f"Position {symbol}: ${current_price:.4f} | "
                    f"P&L: {pnl_pct:+.1f}% | SL=${stop_loss:.4f} | TP=${take_profit:.4f}"
                )

        return closed

    def close_all_eod(self, brain=None):
        """End-of-day: close all remaining open positions and run RCA."""
        logger.info("EOD: closing all open positions")
        open_trades = self.journal.get_open_trades()

        for trade in open_trades:
            symbol = trade["symbol"]
            try:
                current_price = self.alpaca.get_latest_price(symbol)
                self.alpaca.close_position(symbol)

                pnl = (current_price - trade["entry_price"]) * trade["qty"] if current_price > 0 else 0
                rca = "EOD close — position not stopped out or targeted"

                if brain:
                    closed_trade = {**trade, "exit_price": current_price, "pnl": pnl}
                    rca = brain.perform_rca(closed_trade)

                self.journal.close_trade(
                    trade["id"],
                    exit_price=current_price,
                    exit_time=datetime.now().isoformat(),
                    status="eod_close",
                    rca=rca,
                )
                logger.info(f"EOD closed: {symbol} @ ${current_price:.4f} | P&L=${pnl:.2f}")
            except Exception as e:
                logger.error(f"EOD close failed for {symbol}: {e}")
