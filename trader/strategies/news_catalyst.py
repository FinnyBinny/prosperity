"""
News catalyst strategy: finds stocks with fresh (<4 hour) news catalysts
that haven't been fully priced in. Best for catching rapid initial moves.
"""
import logging
import re
from datetime import datetime, timezone, timedelta

import config
from trader.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Keywords that suggest strong positive catalyst
STRONG_POSITIVE = [
    "fda approval", "breakthrough", "acquisition", "merger", "buyout",
    "earnings beat", "revenue beat", "upgrade", "raised guidance",
    "contract awarded", "partnership", "record revenue",
]
STRONG_NEGATIVE = [
    "fda rejection", "investigation", "lawsuit", "recall", "downgrade",
    "earnings miss", "guidance cut", "ceo resign", "fraud", "bankruptcy",
]
# Max age of news to still act on (hours)
MAX_NEWS_AGE_HOURS = 4
# Max premarket gap to avoid chasing already-priced-in news
MAX_PREMARKET_GAP_PCT = 12.0


class NewsCatalystStrategy(BaseStrategy):
    name = "news_catalyst"

    def __init__(self, news_articles: list[dict] = None):
        self.news_articles = news_articles or []

    def scan_candidates(self) -> list[str]:
        """Find stocks with fresh news catalysts."""
        if not self.news_articles:
            logger.debug("No news articles provided to news_catalyst strategy")
            return []

        catalyst_symbols = {}
        now = datetime.now(timezone.utc)

        for article in self.news_articles:
            title = article.get("title", "").lower()
            symbols = self._extract_tickers(article.get("title", "") + " " + article.get("summary", ""))

            if not symbols:
                continue

            # Check news freshness
            age_hours = self._get_article_age_hours(article.get("published", ""), now)
            if age_hours > MAX_NEWS_AGE_HOURS:
                continue

            # Score the catalyst
            catalyst_score = self._score_headline(title)
            if catalyst_score == 0:
                continue

            for sym in symbols:
                if sym not in catalyst_symbols or catalyst_symbols[sym]["score"] < catalyst_score:
                    catalyst_symbols[sym] = {
                        "score": catalyst_score,
                        "headline": article.get("title", ""),
                        "age_hours": age_hours,
                    }

        # Filter to strong catalysts only
        strong_candidates = [
            sym for sym, data in catalyst_symbols.items()
            if data["score"] >= 2
        ]
        logger.info(f"News catalyst scan: {len(strong_candidates)} candidates")
        return strong_candidates[:5]

    def _extract_tickers(self, text: str) -> list[str]:
        """Extract stock ticker symbols from text."""
        # Match $TICKER pattern
        dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', text)
        # Match common all-caps words (potential tickers) - filter known non-tickers
        NOT_TICKERS = {
            "THE", "AND", "FOR", "NOT", "BUT", "WITH", "THIS", "THAT",
            "ARE", "WAS", "HAS", "HAD", "ITS", "ALL", "NEW", "CEO", "CFO",
            "IPO", "FDA", "SEC", "NYSE", "ETF", "YOY", "QOQ", "EPS",
        }
        caps_tickers = [
            w for w in re.findall(r'\b([A-Z]{2,5})\b', text)
            if w not in NOT_TICKERS
        ]
        tickers = list(set(dollar_tickers + caps_tickers))
        # Filter to watchlist + known tradeable symbols
        known = set(config.STOCK_WATCHLIST)
        filtered = [t for t in tickers if t in known or t in dollar_tickers]
        return filtered[:3]

    def _score_headline(self, headline_lower: str) -> int:
        """Score 0-3 based on catalyst strength."""
        for phrase in STRONG_POSITIVE:
            if phrase in headline_lower:
                return 3
        for phrase in STRONG_NEGATIVE:
            if phrase in headline_lower:
                return 2  # Negative news can be tradeable short-term
        # Generic market-moving keywords
        if any(w in headline_lower for w in ["surges", "soars", "jumps", "spikes", "rallies"]):
            return 1
        return 0

    def _get_article_age_hours(self, published_str: str, now: datetime) -> float:
        """Parse published time and return age in hours."""
        if not published_str:
            return MAX_NEWS_AGE_HOURS + 1  # Treat unknown as stale
        try:
            from dateutil import parser as dtparser
            pub = dtparser.parse(published_str)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age = (now - pub).total_seconds() / 3600
            return age
        except Exception:
            return MAX_NEWS_AGE_HOURS + 1
