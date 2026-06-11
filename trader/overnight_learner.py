"""
Overnight learning engine — runs while you sleep (10 PM – 5 AM ET).

Claude acts as a research analyst studying:
  - Today's full market performance + sector flows
  - Earnings calendar for the next 3 trading days
  - New stock candidates based on technical setups forming overnight
  - Macro signals: Fed events, economic data releases, geopolitical news
  - This week's trade history: what worked, what didn't, why

Outputs saved to data/overnight_research.json, which pre-market picks up
as additional context the next morning.
"""
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
import yfinance as yf

import config
from trader.journal import Journal
from trader.brain import ClaudeBrain

logger = logging.getLogger(__name__)

RESEARCH_FILE = config.DATA_DIR / "overnight_research.json"
LEARNING_LOCK = config.DATA_DIR / ".overnight_done"

# Sectors to monitor for rotation signals
SECTOR_ETFS = {
    "Tech": "XLK", "Financials": "XLF", "Energy": "XLE",
    "Healthcare": "XLV", "Consumer": "XLY", "Industrials": "XLI",
    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE",
    "Comm Services": "XLC",
}

# Broader market + volatility gauges
MARKET_GAUGES = ["SPY", "QQQ", "IWM", "DIA", "^VIX", "^TNX"]

# Crypto pairs to study overnight (no market hours)
CRYPTO_STUDY = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"]

# Extended stock universe to scan for setups forming
SCAN_UNIVERSE = [
    # Leveraged ETFs (high volatility)
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXS", "LABU", "LABD",
    "UVXY", "SVXY",
    # High-beta growth
    "MARA", "RIOT", "CLSK", "MSTR", "COIN",
    "NVDA", "AMD", "SMCI", "TSLA", "PLTR", "SOFI",
    # Small caps / momentum favorites
    "SNDL", "NKLA", "LCID", "RIVN", "GOEV", "WKHS",
    "OPEN", "HOOD", "CLOV", "SPCE",
] + config.STOCK_WATCHLIST


class OvernightLearner:
    def __init__(self, journal: Journal, brain: ClaudeBrain):
        self.journal = journal
        self.brain = brain

    def already_ran_tonight(self) -> bool:
        """Prevent running multiple times in the same overnight window."""
        if not LEARNING_LOCK.exists():
            return False
        try:
            last_run = datetime.fromisoformat(LEARNING_LOCK.read_text().strip())
            # Consider it fresh if run within the last 6 hours
            return (datetime.now() - last_run).total_seconds() < 6 * 3600
        except Exception:
            return False

    def mark_complete(self):
        LEARNING_LOCK.write_text(datetime.now().isoformat())

    def run(self) -> dict:
        """Full overnight research cycle. Returns the research bundle."""
        if self.already_ran_tonight():
            logger.info("Overnight learning already completed tonight — skipping")
            return self._load_last_research()

        logger.info("=== OVERNIGHT LEARNING SESSION STARTED ===")
        start = datetime.now()

        research = {
            "date": date.today().isoformat(),
            "started_at": start.isoformat(),
        }

        # 1. Market close summary
        logger.info("[1/6] Analyzing today's market performance...")
        research["market_close"] = self._analyze_market_close()

        # 2. Sector rotation
        logger.info("[2/6] Scanning sector rotation...")
        research["sector_flows"] = self._scan_sectors()

        # 3. Earnings calendar
        logger.info("[3/6] Pulling earnings calendar...")
        research["earnings_calendar"] = self._get_earnings_calendar()

        # 4. Technical setups forming
        logger.info("[4/6] Scanning for technical setups...")
        research["setup_candidates"] = self._scan_for_setups()

        # 5. Crypto overnight analysis
        logger.info("[5/6] Analyzing crypto overnight...")
        research["crypto_overnight"] = self._analyze_crypto()

        # 6. This week's trade performance
        logger.info("[6/6] Reviewing recent trade performance...")
        research["trade_review"] = self._review_recent_trades()

        # Claude synthesizes everything
        logger.info("Claude synthesizing overnight research...")
        synthesis = self._synthesize_with_claude(research)
        research["synthesis"] = synthesis

        research["completed_at"] = datetime.now().isoformat()
        elapsed = (datetime.now() - start).total_seconds()
        logger.info(f"Overnight learning complete in {elapsed:.0f}s")

        # Persist
        RESEARCH_FILE.write_text(json.dumps(research, indent=2, default=str))
        self.mark_complete()

        # Update learnings from synthesis
        self._apply_learnings(synthesis)

        return research

    def _analyze_market_close(self) -> dict:
        """Today's closing snapshot for major indices."""
        result = {}
        for symbol in MARKET_GAUGES:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if len(hist) < 2:
                    continue
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                pct = ((current - prev) / prev) * 100
                week_ago = float(hist["Close"].iloc[0])
                week_pct = ((current - week_ago) / week_ago) * 100
                result[symbol] = {
                    "close": round(current, 2),
                    "day_pct": round(pct, 2),
                    "week_pct": round(week_pct, 2),
                    "volume": int(hist["Volume"].iloc[-1]) if "Volume" in hist else 0,
                }
            except Exception as e:
                logger.debug(f"Market close error for {symbol}: {e}")
        return result

    def _scan_sectors(self) -> list[dict]:
        """Sector ETF performance — reveals where money is rotating."""
        sectors = []
        for name, etf in SECTOR_ETFS.items():
            try:
                ticker = yf.Ticker(etf)
                hist = ticker.history(period="10d")
                if len(hist) < 5:
                    continue
                day_pct = float(((hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2]) * 100)
                week_pct = float(((hist["Close"].iloc[-1] - hist["Close"].iloc[-5]) / hist["Close"].iloc[-5]) * 100)
                sectors.append({
                    "sector": name,
                    "etf": etf,
                    "day_pct": round(day_pct, 2),
                    "week_pct": round(week_pct, 2),
                    "trend": "hot" if week_pct > 2 else "cold" if week_pct < -2 else "neutral",
                })
            except Exception:
                pass
        return sorted(sectors, key=lambda x: x["week_pct"], reverse=True)

    def _get_earnings_calendar(self) -> list[dict]:
        """Stocks reporting earnings in next 3 trading days — huge catalyst potential."""
        events = []
        for days_out in range(1, 4):
            target = date.today() + timedelta(days=days_out)
            # Skip weekends
            if target.weekday() >= 5:
                continue
            for symbol in SCAN_UNIVERSE[:30]:  # Check known watchlist
                try:
                    ticker = yf.Ticker(symbol)
                    cal = ticker.calendar
                    if cal is not None and not cal.empty:
                        earnings_date = cal.get("Earnings Date", [None])[0]
                        if earnings_date and str(earnings_date)[:10] == str(target):
                            info = ticker.info
                            events.append({
                                "symbol": symbol,
                                "date": str(target),
                                "days_out": days_out,
                                "market_cap": info.get("marketCap", 0),
                                "eps_estimate": info.get("forwardEps"),
                                "note": "earnings_catalyst",
                            })
                except Exception:
                    pass
        return events

    def _scan_for_setups(self) -> list[dict]:
        """Find stocks forming technical setups overnight — ready to break tomorrow."""
        candidates = []
        for symbol in SCAN_UNIVERSE:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="30d")
                if len(hist) < 20:
                    continue

                close = hist["Close"]
                volume = hist["Volume"] if "Volume" in hist else None
                current = float(close.iloc[-1])

                # Only look at affordable symbols
                if current > 50 or current < 0.20:
                    continue

                # RSI
                delta = close.diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = (-delta.clip(upper=0)).rolling(14).mean()
                rsi = float((100 - (100 / (1 + gain / loss))).iloc[-1])

                sma20 = float(close.rolling(20).mean().iloc[-1])
                sma5 = float(close.rolling(5).mean().iloc[-1])

                vol_ratio = 1.0
                if volume is not None:
                    avg_vol = float(volume.rolling(20).mean().iloc[-1])
                    vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

                # Score the setup
                setup_score = 0
                setup_tags = []

                # RSI recovering from oversold
                if 30 < rsi < 50:
                    setup_score += 1
                    setup_tags.append("rsi_recovering")

                # Price near/crossing SMA20 (potential breakout)
                price_to_sma = (current - sma20) / sma20 * 100
                if -2 < price_to_sma < 3:
                    setup_score += 1
                    setup_tags.append("near_sma20")

                # Short-term momentum building (5-day SMA crossing up through 20-day)
                if sma5 > sma20 and float(close.rolling(5).mean().iloc[-2]) <= float(close.rolling(20).mean().iloc[-2]):
                    setup_score += 2
                    setup_tags.append("golden_cross_forming")

                # Volume pickup without huge price move (accumulation)
                day_pct = float(((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]) * 100)
                if vol_ratio > 1.5 and abs(day_pct) < 3:
                    setup_score += 1
                    setup_tags.append("volume_accumulation")

                # Tight consolidation (low ATR = coiling for a move)
                tr = (hist["High"] - hist["Low"]).rolling(5).mean().iloc[-1]
                atr_pct = (tr / current) * 100
                if atr_pct < 3:
                    setup_score += 1
                    setup_tags.append("tight_consolidation")

                if setup_score >= 2:
                    candidates.append({
                        "symbol": symbol,
                        "price": round(current, 4),
                        "rsi": round(rsi, 1),
                        "setup_score": setup_score,
                        "setup_tags": setup_tags,
                        "day_pct": round(day_pct, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "atr_pct": round(atr_pct, 2),
                    })
            except Exception:
                pass

        return sorted(candidates, key=lambda x: x["setup_score"], reverse=True)[:15]

    def _analyze_crypto(self) -> dict:
        """Crypto overnight analysis — market never sleeps."""
        result = {}
        for symbol in CRYPTO_STUDY:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="7d", interval="1h")
                if len(hist) < 24:
                    continue
                current = float(hist["Close"].iloc[-1])
                h24_ago = float(hist["Close"].iloc[-24]) if len(hist) >= 24 else current
                h1_ago = float(hist["Close"].iloc[-2])
                pct_24h = ((current - h24_ago) / h24_ago) * 100
                pct_1h = ((current - h1_ago) / h1_ago) * 100
                avg_vol = float(hist["Volume"].rolling(24).mean().iloc[-1]) if "Volume" in hist else 1
                cur_vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist else 1
                result[symbol.replace("-USD", "/USD")] = {
                    "price": round(current, 4),
                    "pct_1h": round(pct_1h, 2),
                    "pct_24h": round(pct_24h, 2),
                    "vol_ratio": round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0,
                    "momentum": "up" if pct_24h > 2 else "down" if pct_24h < -2 else "flat",
                }
            except Exception as e:
                logger.debug(f"Crypto overnight error for {symbol}: {e}")
        return result

    def _review_recent_trades(self) -> dict:
        """Analyze the past 7 days of trades for patterns."""
        recent = self.journal.get_recent_trades(30)
        closed = [t for t in recent if t.get("status") != "open" and t.get("pnl") is not None]

        if not closed:
            return {"message": "No recent closed trades to review"}

        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]

        # Strategy breakdown
        by_strategy = {}
        for t in closed:
            s = t.get("strategy", "unknown")
            if s not in by_strategy:
                by_strategy[s] = {"trades": 0, "pnl": 0, "wins": 0}
            by_strategy[s]["trades"] += 1
            by_strategy[s]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                by_strategy[s]["wins"] += 1

        # Common mistake patterns from RCA
        mistake_tags = []
        for t in losses:
            rca = t.get("rca", "") or ""
            if "Tags:" in rca:
                try:
                    tags_section = rca.split("Tags:")[-1].split("\n")[0]
                    mistake_tags.extend([tag.strip() for tag in tags_section.split(",")])
                except Exception:
                    pass

        return {
            "total_closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(sum(t["pnl"] for t in closed), 2),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
            "best_trade": max(closed, key=lambda x: x["pnl"])["symbol"] if closed else None,
            "worst_trade": min(closed, key=lambda x: x["pnl"])["symbol"] if closed else None,
            "by_strategy": by_strategy,
            "repeat_mistakes": list(set(mistake_tags))[:10],
        }

    def _synthesize_with_claude(self, research: dict) -> dict:
        """Claude reads all overnight research and produces actionable intelligence."""
        # Build a compact summary for the prompt
        sectors_hot = [s for s in research.get("sector_flows", []) if s["trend"] == "hot"]
        sectors_cold = [s for s in research.get("sector_flows", []) if s["trend"] == "cold"]
        setups = research.get("setup_candidates", [])[:8]
        earnings = research.get("earnings_calendar", [])
        crypto = research.get("crypto_overnight", {})
        trade_review = research.get("trade_review", {})
        market_close = research.get("market_close", {})

        # Format for Claude
        spy_data = market_close.get("SPY", {})
        vix_data = market_close.get("^VIX", {})

        prompt = f"""Overnight Market Research Synthesis — {date.today().isoformat()}

MARKET CLOSE:
- SPY: {spy_data.get('close', 'N/A')} ({spy_data.get('day_pct', 0):+.1f}% today, {spy_data.get('week_pct', 0):+.1f}% this week)
- VIX: {vix_data.get('close', 'N/A')} ({vix_data.get('day_pct', 0):+.1f}%)

HOT SECTORS (money flowing in): {', '.join(f"{s['sector']} ({s['week_pct']:+.1f}%)" for s in sectors_hot[:3])}
COLD SECTORS (money flowing out): {', '.join(f"{s['sector']} ({s['week_pct']:+.1f}%)" for s in sectors_cold[:3])}

EARNINGS NEXT 3 DAYS: {json.dumps([{k: v for k, v in e.items() if k != 'market_cap'} for e in earnings], indent=2) if earnings else 'None found'}

TECHNICAL SETUPS FORMING:
{json.dumps(setups, indent=2)}

CRYPTO OVERNIGHT:
{json.dumps(crypto, indent=2)}

RECENT TRADE PERFORMANCE:
- Win rate: {trade_review.get('win_rate', 0)}% over {trade_review.get('total_closed', 0)} trades
- P&L: ${trade_review.get('total_pnl', 0):+.2f}
- Repeat mistakes: {trade_review.get('repeat_mistakes', [])}
- By strategy: {json.dumps(trade_review.get('by_strategy', {}), indent=2)}

Based on this overnight research, synthesize tomorrow's trading plan. Return JSON:
{{
  "market_outlook": "bullish|bearish|neutral|volatile",
  "outlook_reasoning": "2-3 sentences on tomorrow's likely market character",
  "recommended_strategy_tomorrow": "momentum|news_catalyst|crypto_scalp|stay_flat",
  "pre_watchlist": [
    {{
      "symbol": "TICKER",
      "reason": "why this is interesting tomorrow",
      "setup": "what technical/fundamental setup is forming",
      "asset_class": "stock|crypto",
      "priority": 1
    }}
  ],
  "sector_to_watch": "sector name and why",
  "risk_factors": ["risk1", "risk2"],
  "new_insight": "one novel insight from tonight's research that improves future trading",
  "strategy_adjustment": "any adjustment to current strategy based on recent performance, or null",
  "patterns_learned": ["pattern or rule to add to learnings, or empty list"]
}}

pre_watchlist: max 5 symbols, rank by conviction. Only include setups that look genuinely strong.
patterns_learned: concrete, actionable rules like existing ones in the system."""

        try:
            response = self.brain._call_api(
                model=config.ANALYSIS_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
            )
            return self.brain._parse_json(response)
        except Exception as e:
            logger.error(f"Claude synthesis failed: {e}")
            return {
                "market_outlook": "neutral",
                "outlook_reasoning": f"Synthesis unavailable: {e}",
                "recommended_strategy_tomorrow": "stay_flat",
                "pre_watchlist": [],
                "sector_to_watch": "",
                "risk_factors": ["AI synthesis failed"],
                "new_insight": "",
                "strategy_adjustment": None,
                "patterns_learned": [],
            }

    def _apply_learnings(self, synthesis: dict):
        """Persist any new patterns Claude discovered overnight."""
        for pattern in synthesis.get("patterns_learned", []):
            if pattern and len(pattern) > 10:
                self.journal.add_learning("rules", pattern)
                logger.info(f"New rule from overnight learning: {pattern}")
        if synthesis.get("new_insight"):
            self.journal.add_learning(
                "winning_patterns",
                f"[Overnight insight] {synthesis['new_insight']}"
            )

    def _load_last_research(self) -> dict:
        if RESEARCH_FILE.exists():
            try:
                return json.loads(RESEARCH_FILE.read_text())
            except Exception:
                pass
        return {}

    def get_morning_brief(self) -> dict:
        """Pre-market reads last night's research to add context."""
        return self._load_last_research()
