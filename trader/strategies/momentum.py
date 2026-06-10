"""
Momentum breakout strategy: finds penny stocks breaking out on volume with
clear technical setups. Best for capturing explosive intraday moves.
"""
import logging

import yfinance as yf

import config
from trader.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Additional pennystocks to dynamically screen beyond the base watchlist
EXTRA_SCAN_SYMBOLS = [
    "SNDL", "CTRM", "TTOO", "ASTI", "MARA", "RIOT", "CLSK",
    "NKLA", "WKHS", "GOEV", "IDEX", "SOXS", "SOXL",
    "TQQQ", "SQQQ", "UVXY", "VIXY",
]


class MomentumStrategy(BaseStrategy):
    name = "momentum_breakout"

    # Breakout filters
    MIN_VOLUME_RATIO = 1.5      # Volume must be 1.5x average
    MIN_PRICE_CHANGE_PCT = 2.0  # At least 2% move
    MAX_PRICE = 10.0            # Focus on lower-priced symbols
    MIN_PRICE = 0.50            # Avoid sub-penny garbage

    def scan_candidates(self) -> list[str]:
        """Screen symbols for momentum characteristics."""
        candidates = []
        symbols = list(set(config.STOCK_WATCHLIST + EXTRA_SCAN_SYMBOLS))

        for symbol in symbols:
            try:
                score = self._score_momentum(symbol)
                if score >= 2:  # Need at least 2 momentum signals
                    candidates.append(symbol)
                    logger.debug(f"Momentum candidate: {symbol} (score={score})")
            except Exception as e:
                logger.debug(f"Scan error for {symbol}: {e}")

        logger.info(f"Momentum scan: {len(candidates)} candidates from {len(symbols)} symbols")
        return candidates

    def _score_momentum(self, symbol: str) -> int:
        """Score a symbol 0-5 based on momentum signals."""
        score = 0
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="20d", interval="1d")

        if hist.empty or len(hist) < 5:
            return 0

        current = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])

        # Filter by price range
        if not (self.MIN_PRICE <= current <= self.MAX_PRICE):
            return 0

        pct_change = ((current - prev) / prev) * 100

        # Signal 1: Significant price move
        if pct_change >= self.MIN_PRICE_CHANGE_PCT:
            score += 1
        if pct_change >= 5.0:
            score += 1  # Extra point for strong move

        # Signal 2: Volume surge
        if "Volume" in hist.columns:
            today_vol = float(hist["Volume"].iloc[-1])
            avg_vol = float(hist["Volume"].rolling(20).mean().iloc[-1])
            if avg_vol > 0 and today_vol / avg_vol >= self.MIN_VOLUME_RATIO:
                score += 1
            if avg_vol > 0 and today_vol / avg_vol >= 3.0:
                score += 1  # Extreme volume = extra point

        # Signal 3: Price above 20-day SMA (trend confirmation)
        sma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
        if current > sma20:
            score += 1

        return score
