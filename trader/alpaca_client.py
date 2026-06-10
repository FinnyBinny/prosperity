"""
Alpaca Markets API wrapper — handles orders, positions, account info,
and market data for both stocks and crypto.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    StopLimitOrderRequest, GetOrdersRequest, ClosePositionRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
import pandas as pd

import config

logger = logging.getLogger(__name__)


class AlpacaClient:
    def __init__(self):
        self.trading = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=(config.ALPACA_MODE == "paper"),
        )
        self.stock_data = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
        self.crypto_data = CryptoHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
        logger.info(f"Alpaca client initialized [{config.ALPACA_MODE.upper()} MODE]")

    def get_account(self) -> dict:
        acct = self.trading.get_account()
        return {
            "portfolio_value": float(acct.portfolio_value),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "equity": float(acct.equity),
            "pnl_today": float(acct.equity) - float(acct.last_equity),
            "daytrade_count": acct.daytrade_count,
            "pattern_day_trader": acct.pattern_day_trader,
        }

    def get_positions(self) -> list[dict]:
        positions = self.trading.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "side": p.side.value,
            }
            for p in positions
        ]

    def is_market_open(self) -> bool:
        clock = self.trading.get_clock()
        return clock.is_open

    def get_market_hours(self) -> dict:
        clock = self.trading.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open.isoformat(),
            "next_close": clock.next_close.isoformat(),
        }

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        time_in_force: str = "gtc",
    ) -> dict:
        """Place a market order. Returns order dict."""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if time_in_force == "gtc" else TimeInForce.DAY

        # Crypto uses fractional qty, stocks need whole shares (or fractional if enabled)
        is_crypto = "/" in symbol
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
        )
        order = self.trading.submit_order(request)
        logger.info(f"Market order placed: {side.upper()} {qty} {symbol} | ID: {order.id}")
        return self._order_to_dict(order)

    def place_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        time_in_force: str = "day",
    ) -> dict:
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC
        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            limit_price=round(limit_price, 2),
            time_in_force=tif,
        )
        order = self.trading.submit_order(request)
        logger.info(f"Limit order: {side.upper()} {qty} {symbol} @ ${limit_price:.4f}")
        return self._order_to_dict(order)

    def place_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> dict:
        """One-cancels-other bracket order: TP and SL set at entry."""
        from alpaca.trading.requests import TakeProfitRequest, StopLossRequest
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            order_class="bracket",
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2)),
        )
        order = self.trading.submit_order(request)
        logger.info(
            f"Bracket order: {side.upper()} {qty} {symbol} "
            f"| TP: ${take_profit_price:.4f} | SL: ${stop_loss_price:.4f}"
        )
        return self._order_to_dict(order)

    def close_position(self, symbol: str) -> dict:
        try:
            order = self.trading.close_position(symbol)
            logger.info(f"Position closed: {symbol}")
            return self._order_to_dict(order)
        except Exception as e:
            logger.error(f"Failed to close {symbol}: {e}")
            raise

    def close_all_positions(self):
        self.trading.close_all_positions(cancel_orders=True)
        logger.info("All positions closed")

    def cancel_order(self, order_id: str):
        self.trading.cancel_order_by_id(order_id)
        logger.info(f"Order cancelled: {order_id}")

    def get_order(self, order_id: str) -> dict:
        order = self.trading.get_order_by_id(order_id)
        return self._order_to_dict(order)

    def get_latest_price(self, symbol: str) -> float:
        """Get the latest price for a symbol."""
        try:
            if "/" in symbol:
                from alpaca.data.requests import CryptoLatestQuoteRequest
                req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
                quote = self.crypto_data.get_crypto_latest_quote(req)
                return float(quote[symbol].ask_price)
            else:
                from alpaca.data.requests import StockLatestTradeRequest
                req = StockLatestTradeRequest(symbol_or_symbols=symbol)
                trade = self.stock_data.get_stock_latest_trade(req)
                return float(trade[symbol].price)
        except Exception as e:
            logger.warning(f"Could not get price for {symbol}: {e}")
            return 0.0

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 50,
    ) -> pd.DataFrame:
        """Get OHLCV bars as a DataFrame."""
        end = datetime.now()
        start = end - timedelta(days=limit * 2)
        try:
            if "/" in symbol:
                tf = self._parse_timeframe(timeframe)
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf,
                    start=start,
                    end=end,
                    limit=limit,
                )
                bars = self.crypto_data.get_crypto_bars(req)
            else:
                tf = self._parse_timeframe(timeframe)
                req = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=tf,
                    start=start,
                    end=end,
                    limit=limit,
                )
                bars = self.stock_data.get_stock_bars(req)
            df = bars.df
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol, level="symbol")
            return df.tail(limit)
        except Exception as e:
            logger.warning(f"Failed to get bars for {symbol}: {e}")
            return pd.DataFrame()

    def _parse_timeframe(self, tf_str: str) -> TimeFrame:
        mapping = {
            "1Min": TimeFrame.Minute,
            "5Min": TimeFrame(5, "Min"),
            "15Min": TimeFrame(15, "Min"),
            "1Hour": TimeFrame.Hour,
            "1Day": TimeFrame.Day,
        }
        return mapping.get(tf_str, TimeFrame.Day)

    def _order_to_dict(self, order) -> dict:
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty or 0),
            "filled_qty": float(order.filled_qty or 0),
            "filled_avg_price": float(order.filled_avg_price or 0),
            "side": order.side.value,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else "",
        }
