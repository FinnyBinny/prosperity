"""
Market intelligence engine: aggregates news, social sentiment, and technical
signals to produce a structured briefing for the AI trader.
"""
import logging
import json
import re
from datetime import datetime, timedelta
from typing import Optional

import feedparser
import requests
import yfinance as yf
import pandas as pd

import config

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    # Reddit's public JSON API — no OAuth needed, just a browser-like User-Agent
    _REDDIT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; prosperity-trader/1.0)"}
    # StockTwits trending — fully public, no key required
    _STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"

    def __init__(self):
        pass

    def fetch_news(self, max_articles: int = 30) -> list[dict]:
        """Fetch headlines from RSS feeds and NewsAPI."""
        articles = []

        # RSS feeds
        for feed_url in config.NEWS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:8]:
                    articles.append({
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", "")[:300],
                        "published": entry.get("published", ""),
                        "source": feed.feed.get("title", feed_url),
                        "link": entry.get("link", ""),
                    })
            except Exception as e:
                logger.debug(f"RSS feed error {feed_url}: {e}")

        # NewsAPI (if key available)
        if config.NEWS_API_KEY:
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={
                        "category": "business",
                        "language": "en",
                        "pageSize": 20,
                        "apiKey": config.NEWS_API_KEY,
                    },
                    timeout=10,
                )
                if resp.ok:
                    for a in resp.json().get("articles", []):
                        articles.append({
                            "title": a.get("title", ""),
                            "summary": a.get("description", "")[:300],
                            "published": a.get("publishedAt", ""),
                            "source": a.get("source", {}).get("name", ""),
                            "link": a.get("url", ""),
                        })
            except Exception as e:
                logger.debug(f"NewsAPI error: {e}")

        # Finnhub market news
        if config.FINNHUB_API_KEY:
            try:
                resp = requests.get(
                    "https://finnhub.io/api/v1/news",
                    params={"category": "general", "token": config.FINNHUB_API_KEY},
                    timeout=10,
                )
                if resp.ok:
                    for a in resp.json()[:15]:
                        articles.append({
                            "title": a.get("headline", ""),
                            "summary": a.get("summary", "")[:300],
                            "published": datetime.fromtimestamp(a.get("datetime", 0)).isoformat(),
                            "source": a.get("source", "Finnhub"),
                            "link": a.get("url", ""),
                        })
            except Exception as e:
                logger.debug(f"Finnhub error: {e}")

        seen = set()
        unique = []
        for a in articles:
            if a["title"] not in seen and a["title"]:
                seen.add(a["title"])
                unique.append(a)
        return unique[:max_articles]

    def fetch_reddit_sentiment(self, max_posts: int = 50) -> list[dict]:
        """
        Fetch hot posts from trading subreddits via Reddit's public JSON API.
        No API credentials needed — uses the same unauthenticated endpoint
        that any browser sees at reddit.com/r/wallstreetbets/hot.json.
        """
        posts = []
        per_sub = max(5, max_posts // len(config.SENTIMENT_SUBREDDITS))
        for sub_name in config.SENTIMENT_SUBREDDITS:
            try:
                url = f"https://www.reddit.com/r/{sub_name}/hot.json"
                resp = requests.get(
                    url,
                    headers=self._REDDIT_HEADERS,
                    params={"limit": per_sub},
                    timeout=10,
                )
                if not resp.ok:
                    logger.debug(f"Reddit {sub_name}: HTTP {resp.status_code}")
                    continue
                children = resp.json().get("data", {}).get("children", [])
                for child in children:
                    d = child.get("data", {})
                    score = d.get("score", 0)
                    if score < 20:
                        continue
                    posts.append({
                        "subreddit": sub_name,
                        "title": d.get("title", ""),
                        "score": score,
                        "num_comments": d.get("num_comments", 0),
                        "url": d.get("url", ""),
                        "created_utc": datetime.fromtimestamp(
                            d.get("created_utc", 0)
                        ).isoformat(),
                    })
            except Exception as e:
                logger.debug(f"Reddit error for r/{sub_name}: {e}")
        return sorted(posts, key=lambda x: x["score"], reverse=True)[:max_posts]

    def fetch_stocktwits_trending(self) -> list[dict]:
        """
        StockTwits trending symbols — free public endpoint, no auth required.
        Returns list of {symbol, watchlist_count, messages_count}.
        """
        try:
            resp = requests.get(self._STOCKTWITS_TRENDING_URL, timeout=10)
            if not resp.ok:
                return []
            symbols = resp.json().get("symbols", [])
            return [
                {
                    "symbol": s.get("symbol", ""),
                    "title": s.get("title", ""),
                    "watchlist_count": s.get("watchlist_count", 0),
                }
                for s in symbols
            ]
        except Exception as e:
            logger.debug(f"StockTwits trending error: {e}")
            return []

    def get_symbol_news(self, symbol: str) -> list[dict]:
        """Get news specific to a ticker symbol."""
        clean = symbol.replace("/", "")
        articles = []
        if config.FINNHUB_API_KEY:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                resp = requests.get(
                    f"https://finnhub.io/api/v1/company-news",
                    params={
                        "symbol": clean,
                        "from": week_ago,
                        "to": today,
                        "token": config.FINNHUB_API_KEY,
                    },
                    timeout=10,
                )
                if resp.ok:
                    for a in resp.json()[:10]:
                        articles.append({
                            "title": a.get("headline", ""),
                            "summary": a.get("summary", "")[:300],
                            "published": datetime.fromtimestamp(
                                a.get("datetime", 0)
                            ).isoformat(),
                            "source": a.get("source", "Finnhub"),
                        })
            except Exception as e:
                logger.debug(f"Symbol news error for {symbol}: {e}")
        return articles

    def get_technical_data(self, symbol: str, from_alpaca=None) -> dict:
        """Calculate technical indicators for a symbol."""
        try:
            if from_alpaca:
                df = from_alpaca.get_bars(symbol, "1Day", limit=50)
            else:
                ticker = symbol.replace("/", "-")
                df = yf.download(ticker, period="60d", interval="1d", progress=False)
                df.columns = [c.lower() for c in df.columns]

            if df.empty or len(df) < 10:
                return {}

            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"] if "volume" in df.columns else pd.Series()

            # Simple technical indicators without pandas_ta dependency issues
            def sma(series, window):
                return series.rolling(window).mean().iloc[-1]

            def ema(series, window):
                return series.ewm(span=window, adjust=False).mean().iloc[-1]

            current = float(close.iloc[-1])
            sma20 = float(sma(close, 20))
            sma50 = float(sma(close, 50)) if len(close) >= 50 else None
            ema9 = float(ema(close, 9))
            ema21 = float(ema(close, 21))

            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            rsi = float((100 - (100 / (1 + rs))).iloc[-1])

            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = float((ema12 - ema26).iloc[-1])
            signal_line = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])
            macd_hist = macd_line - signal_line

            # Bollinger Bands
            bb_mid = sma(close, 20)
            bb_std = float(close.rolling(20).std().iloc[-1])
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            # ATR (volatility)
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = (atr / current) * 100

            # Volume analysis
            vol_avg = None
            vol_ratio = None
            if not volume.empty:
                vol_avg = float(volume.rolling(20).mean().iloc[-1])
                vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg else 1.0

            # Price change
            pct_1d = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
            pct_5d = float(((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100) if len(close) >= 5 else 0

            # Trend
            trend = "bullish" if current > sma20 > (sma50 or 0) else \
                    "bearish" if current < sma20 < (sma50 or float('inf')) else "neutral"

            return {
                "current_price": round(current, 4),
                "change_1d_pct": round(pct_1d, 2),
                "change_5d_pct": round(pct_5d, 2),
                "rsi": round(rsi, 1),
                "macd": round(macd_line, 4),
                "macd_signal": round(signal_line, 4),
                "macd_histogram": round(macd_hist, 4),
                "sma20": round(sma20, 4),
                "sma50": round(sma50, 4) if sma50 else None,
                "ema9": round(ema9, 4),
                "ema21": round(ema21, 4),
                "bb_upper": round(bb_upper, 4),
                "bb_lower": round(bb_lower, 4),
                "atr": round(atr, 4),
                "atr_pct": round(atr_pct, 2),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "trend": trend,
                "above_sma20": current > sma20,
                "rsi_oversold": rsi < 30,
                "rsi_overbought": rsi > 70,
                "macd_bullish_cross": macd_line > signal_line and macd_hist > 0,
            }
        except Exception as e:
            logger.warning(f"Technical data error for {symbol}: {e}")
            return {}

    def get_market_overview(self) -> dict:
        """Broad market snapshot: SPY, QQQ, VIX."""
        overview = {}
        for ticker, key in [("SPY", "spy"), ("QQQ", "qqq"), ("^VIX", "vix")]:
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="5d")
                if not hist.empty:
                    current = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    overview[key] = {
                        "price": round(current, 2),
                        "change_pct": round(((current - prev) / prev) * 100, 2),
                    }
            except Exception as e:
                logger.debug(f"Market overview error for {ticker}: {e}")
        return overview

    def scan_for_movers(self) -> list[dict]:
        """Find today's biggest movers from our watchlists."""
        candidates = []
        symbols = []
        if config.ENABLE_STOCKS:
            symbols += config.STOCK_WATCHLIST
        if config.ENABLE_CRYPTO:
            symbols += [s.replace("/", "-") for s in config.CRYPTO_WATCHLIST]

        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="2d", interval="1d")
                if len(hist) >= 2:
                    current = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    pct = ((current - prev) / prev) * 100
                    vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist else 0
                    avg_vol = float(hist["Volume"].mean()) if "Volume" in hist else 1
                    candidates.append({
                        "symbol": symbol.replace("-", "/") if "BTC" in symbol or
                                  any(c in symbol for c in ["ETH", "SOL", "DOGE", "AVAX"]) else symbol,
                        "price": round(current, 4),
                        "change_pct": round(pct, 2),
                        "volume": vol,
                        "vol_ratio": round(vol / avg_vol, 1) if avg_vol > 0 else 1.0,
                    })
            except Exception:
                pass

        return sorted(candidates, key=lambda x: abs(x["change_pct"]), reverse=True)[:10]

    def build_market_brief(self) -> dict:
        """Compile a full market briefing for AI analysis."""
        logger.info("Building market brief...")
        return {
            "timestamp": datetime.now().isoformat(),
            "market_overview": self.get_market_overview(),
            "top_news": self.fetch_news(20),
            "reddit_sentiment": self.fetch_reddit_sentiment(30),
            "stocktwits_trending": self.fetch_stocktwits_trending(),
            "top_movers": self.scan_for_movers(),
        }
