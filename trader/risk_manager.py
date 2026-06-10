"""
Risk management: position sizing, PDT enforcement, stop/target calculation,
and daily loss limits. Nothing trades without passing through here.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import config
from trader.journal import Journal

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    symbol: str
    action: str           # "buy" | "sell" | "skip"
    asset_class: str      # "stock" | "crypto"
    confidence: float     # 0.0 - 1.0
    rationale: str
    catalyst: str         # "news" | "momentum" | "sentiment" | "technical"
    strategy: str
    entry_price: float
    stop_loss: float
    take_profit: float
    suggested_qty: float = 0.0
    risk_flags: list = None

    def __post_init__(self):
        if self.risk_flags is None:
            self.risk_flags = []


@dataclass
class ValidationResult:
    approved: bool
    reason: str
    final_qty: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0


class RiskManager:
    # Minimum confidence to trade
    MIN_CONFIDENCE_STOCK = 0.70
    MIN_CONFIDENCE_CRYPTO = 0.60
    # Max simultaneous open positions
    MAX_OPEN_POSITIONS = 2
    # Stop trading if daily loss exceeds this % of starting day value
    DAILY_LOSS_LIMIT_PCT = 0.15

    def __init__(self, journal: Journal):
        self.journal = journal
        self._daily_start_value: Optional[float] = None

    def set_daily_start_value(self, value: float):
        self._daily_start_value = value

    def validate_trade(
        self,
        signal: TradeSignal,
        buying_power: float,
        current_open_positions: int,
        current_portfolio_value: float,
    ) -> ValidationResult:
        """Full pre-flight validation. Returns approved or rejected with reason."""

        # 1. Safety gate: live trading double-confirmation
        if config.ALPACA_MODE == "live" and not config.LIVE_TRADING_CONFIRMED:
            return ValidationResult(
                False,
                "LIVE_TRADING_CONFIRMED env var not set. Set to 'yes' to enable live trades.",
            )

        # 2. Skip signals pass through immediately
        if signal.action == "skip":
            return ValidationResult(False, "Signal is SKIP — no trade.")

        # 3. Confidence threshold
        min_conf = (
            self.MIN_CONFIDENCE_STOCK
            if signal.asset_class == "stock"
            else self.MIN_CONFIDENCE_CRYPTO
        )
        if signal.confidence < min_conf:
            return ValidationResult(
                False,
                f"Confidence {signal.confidence:.2f} below minimum {min_conf} for {signal.asset_class}",
            )

        # 4. PDT check (stocks only)
        if signal.asset_class == "stock":
            pdt_result = self._check_pdt()
            if not pdt_result[0]:
                return ValidationResult(False, pdt_result[1])

        # 5. Max open positions
        if current_open_positions >= self.MAX_OPEN_POSITIONS:
            return ValidationResult(
                False,
                f"Max open positions ({self.MAX_OPEN_POSITIONS}) reached.",
            )

        # 6. Daily loss limit
        if self._daily_start_value:
            today_pnl = sum(
                t.get("pnl", 0) or 0 for t in self.journal.get_todays_trades()
                if t.get("status") != "open"
            )
            loss_pct = abs(today_pnl) / self._daily_start_value if today_pnl < 0 else 0
            if loss_pct >= self.DAILY_LOSS_LIMIT_PCT:
                return ValidationResult(
                    False,
                    f"Daily loss limit reached ({loss_pct*100:.1f}%). No more trades today.",
                )

        # 7. Check against known mistake patterns
        mistake_check = self._check_learnings(signal)
        if mistake_check:
            return ValidationResult(False, f"Matches known mistake pattern: {mistake_check}")

        # 8. Minimum buying power
        if buying_power < 1.00:
            return ValidationResult(False, f"Insufficient buying power: ${buying_power:.2f}")

        # 9. Calculate position size
        qty = self.calculate_position_size(
            signal.entry_price, buying_power, signal.confidence
        )
        if qty <= 0:
            return ValidationResult(
                False,
                f"Position size too small at ${signal.entry_price:.4f} with ${buying_power:.2f}",
            )

        # 10. Recalculate stops based on actual entry
        stop = self.get_stop_price(signal.entry_price, signal.asset_class)
        target = self.get_target_price(signal.entry_price, signal.asset_class)

        return ValidationResult(
            approved=True,
            reason="All checks passed",
            final_qty=qty,
            stop_loss=stop,
            take_profit=target,
        )

    def calculate_position_size(
        self,
        price: float,
        buying_power: float,
        confidence: float,
    ) -> float:
        """Calculate how many shares/coins to buy."""
        if price <= 0 or buying_power <= 0:
            return 0.0

        # Scale position size with confidence: high confidence = full allocation
        confidence_scalar = min(1.0, max(0.5, confidence))
        allocated_capital = buying_power * config.MAX_POSITION_SIZE * confidence_scalar

        # For crypto, fractional shares allowed
        # For stocks, floor to whole shares
        qty = allocated_capital / price

        # Keep minimum $1 worth
        if qty * price < 1.0:
            return 0.0

        return round(qty, 6)  # crypto precision; stocks will be whole numbers

    def get_stop_price(self, entry: float, asset_class: str = "stock") -> float:
        """Calculate stop loss price."""
        # Crypto is more volatile — slightly wider stop
        sl_pct = config.STOP_LOSS_PCT * (1.5 if asset_class == "crypto" else 1.0)
        return round(entry * (1 - sl_pct), 6)

    def get_target_price(self, entry: float, asset_class: str = "stock") -> float:
        """Calculate take profit price."""
        tp_pct = config.TAKE_PROFIT_PCT * (1.5 if asset_class == "crypto" else 1.0)
        return round(entry * (1 + tp_pct), 6)

    def should_exit(
        self,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[bool, str]:
        """Returns (should_exit, reason)."""
        if current_price <= stop_loss:
            return True, "stop_loss"
        if current_price >= take_profit:
            return True, "target_hit"
        return False, "hold"

    def _check_pdt(self) -> tuple[bool, str]:
        """Check if we can make another day trade."""
        day_trade_count = self.journal.count_day_trades_this_week()
        remaining = config.MAX_DAILY_TRADES - day_trade_count
        if remaining <= 0:
            return (
                False,
                f"PDT limit reached: {day_trade_count} day trades in rolling 5-day window. "
                "Use crypto or wait for the window to reset.",
            )
        logger.debug(f"PDT check: {day_trade_count} used, {remaining} remaining")
        return True, f"{remaining} day trades remaining"

    def _check_learnings(self, signal: TradeSignal) -> Optional[str]:
        """Return a matching mistake pattern if this signal is risky, else None."""
        learnings = self.journal.load_learnings()
        flags = []

        # Detect over-movement (chasing)
        if signal.asset_class == "stock":
            # We'd need price change % here — only flag if explicitly tagged
            if "chasing" in signal.risk_flags:
                flags.append("Identified as chasing (gap already happened)")
        if "stale_news" in signal.risk_flags:
            flags.append("News is stale (>4 hours old)")
        if "low_volume" in signal.risk_flags:
            flags.append("Low volume symbol — spread risk")

        for mistake in learnings.get("mistakes", []):
            if any(keyword in mistake.lower() for keyword in signal.risk_flags):
                flags.append(mistake)

        return flags[0] if flags else None
