"""
Crypto scalp strategy: 24/7 trading on Alpaca crypto pairs.
No PDT rules, fractional shares, lower confidence threshold.
Targets momentum and volume surges on BTC, ETH, SOL, DOGE.
"""
import logging

import config
from trader.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoScalpStrategy(BaseStrategy):
    name = "crypto_scalp"

    # Crypto-specific filters
    MIN_VOLUME_RATIO = 1.3    # Lower bar since crypto is always active
    MIN_MOVE_PCT = 1.5        # At least 1.5% move to be interesting

    def scan_candidates(self) -> list[str]:
        """Return all configured crypto symbols — we'll let the AI filter."""
        if not config.ENABLE_CRYPTO:
            return []
        logger.info(f"Crypto scan: monitoring {len(config.CRYPTO_WATCHLIST)} pairs")
        return config.CRYPTO_WATCHLIST

    def describe(self) -> str:
        return (
            f"Strategy: {self.name} | "
            f"Pairs: {', '.join(config.CRYPTO_WATCHLIST)} | "
            "No PDT restrictions | 24/7"
        )
