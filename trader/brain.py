"""
Claude AI integration — the decision-making core.
Claude acts as a Citadel-style quant analyst: receives structured market context,
approves/rejects trade signals, and performs post-trade root cause analysis.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import anthropic

import config
from trader.journal import Journal

logger = logging.getLogger(__name__)


@dataclass
class PremarketAnalysis:
    watchlist: list[dict]   # [{symbol, priority, rationale, asset_class}]
    market_bias: str        # "bullish" | "bearish" | "neutral" | "volatile"
    macro_summary: str
    key_risks: list[str]
    recommended_strategy: str


@dataclass
class TradeDecision:
    action: str           # "buy" | "skip"
    confidence: float     # 0.0 - 1.0
    rationale: str
    stop_loss: float
    take_profit: float
    risk_flags: list[str]
    entry_notes: str


class ClaudeBrain:
    def __init__(self, journal: Journal):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.journal = journal

    def _build_system_prompt(self) -> str:
        stats = self.journal.get_stats()
        learnings = self.journal.load_learnings()
        rules_text = "\n".join(f"  - {r}" for r in learnings.get("rules", []))
        mistakes_text = "\n".join(f"  - {m}" for m in learnings.get("mistakes", [])[:10])
        winning_text = "\n".join(f"  - {w}" for w in learnings.get("winning_patterns", [])[:5])

        goal_progress = ""
        if stats["total_trades"] > 0:
            goal_progress = f"Progress: ${stats['total_pnl']:+.2f} P&L over {stats['total_trades']} trades ({stats['win_rate']}% win rate)"

        return f"""You are a disciplined day trader managing a small account targeting massive returns.
Account profile:
- Starting capital: ${config.STARTING_CAPITAL:.2f}
- Target: ${config.TARGET_CAPITAL:.2f} (+{((config.TARGET_CAPITAL/config.STARTING_CAPITAL)-1)*100:.0f}%)
- Mode: {'PAPER TRADING' if config.ALPACA_MODE == 'paper' else '*** LIVE MONEY ***'}
- {goal_progress or 'No trades yet'}

Trading philosophy (think like a Citadel quant on a micro account):
1. Capital preservation first — a 3% loss on $20 is $0.60; don't compound mistakes
2. Only trade setups with clear edge: strong catalyst + technical confirmation
3. Position sizing: aggressive (up to 90%) because the account is too small to diversify meaningfully
4. Crypto first when near PDT limits — no pattern day trader restrictions
5. If you're not sure, do NOTHING. "No trade" is a valid trade.
6. React to news within the first 2 hours or skip — old catalysts are priced in
7. Think about what human traders will do — front-run their emotional reactions

Active trading rules (MUST follow):
{rules_text}

Known mistake patterns to NEVER repeat:
{mistakes_text if mistakes_text else '  - None yet (learning...)'}

Winning patterns observed:
{winning_text if winning_text else '  - None yet (learning...)'}

Response format: Always respond with valid JSON only. No markdown, no explanation outside JSON.
"""

    def analyze_premarket(self, context: dict) -> PremarketAnalysis:
        """Morning analysis: given full market context, return ranked watchlist."""
        logger.info("Running pre-market AI analysis...")

        news_text = "\n".join(
            f"- [{a['source']}] {a['title']}" for a in context.get("top_news", [])[:15]
        )
        reddit_text = "\n".join(
            f"- r/{p['subreddit']}: {p['title']} (score:{p['score']})"
            for p in context.get("reddit_sentiment", [])[:10]
        )
        movers_text = json.dumps(context.get("top_movers", [])[:8], indent=2)
        overview = context.get("market_overview", {})

        user_message = f"""Pre-market analysis for {context.get('timestamp', 'today')}.

MARKET OVERVIEW:
{json.dumps(overview, indent=2)}

TOP NEWS (last 4 hours):
{news_text}

REDDIT SENTIMENT:
{reddit_text}

PRE-MARKET MOVERS:
{movers_text}

Based on this intelligence, provide your pre-market analysis. Return JSON:
{{
  "market_bias": "bullish|bearish|neutral|volatile",
  "macro_summary": "2-3 sentence overview of market conditions",
  "key_risks": ["risk1", "risk2", "risk3"],
  "recommended_strategy": "momentum|news_catalyst|crypto_scalp|stay_flat",
  "watchlist": [
    {{
      "symbol": "TICKER",
      "priority": 1,
      "asset_class": "stock|crypto",
      "rationale": "why this symbol today",
      "catalyst": "news|momentum|sentiment|technical",
      "notes": "specific setup to watch for"
    }}
  ]
}}

Rank watchlist by conviction (1=highest). Max 5 symbols. Only include symbols with STRONG setups.
If market conditions are bad, return empty watchlist and "stay_flat" strategy."""

        try:
            response = self._call_api(
                model=config.ANALYSIS_MODEL,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=2000,
            )
            data = self._parse_json(response)
            return PremarketAnalysis(
                watchlist=data.get("watchlist", []),
                market_bias=data.get("market_bias", "neutral"),
                macro_summary=data.get("macro_summary", ""),
                key_risks=data.get("key_risks", []),
                recommended_strategy=data.get("recommended_strategy", "stay_flat"),
            )
        except Exception as e:
            logger.error(f"Pre-market analysis failed: {e}")
            return PremarketAnalysis(
                watchlist=[], market_bias="neutral",
                macro_summary="Analysis unavailable",
                key_risks=["AI analysis failed — staying flat"],
                recommended_strategy="stay_flat",
            )

    def make_trade_decision(
        self,
        symbol: str,
        asset_class: str,
        technical_data: dict,
        news: list[dict],
        sentiment: dict,
        macro: dict,
        pdt_remaining: int,
        buying_power: float,
    ) -> TradeDecision:
        """Real-time trade decision for a specific symbol."""
        logger.info(f"Making trade decision for {symbol}...")

        news_text = "\n".join(f"- {a['title']}" for a in news[:5]) if news else "No recent news"

        user_message = f"""Trade decision request: {symbol} ({asset_class.upper()})

TECHNICAL ANALYSIS:
{json.dumps(technical_data, indent=2)}

RECENT NEWS:
{news_text}

SOCIAL SENTIMENT: {json.dumps(sentiment, indent=2)}

MACRO CONTEXT: {json.dumps(macro, indent=2)}

ACCOUNT STATE:
- Buying power: ${buying_power:.2f}
- PDT trades remaining this week: {pdt_remaining}
- Asset class: {asset_class} {'(no PDT limit)' if asset_class == 'crypto' else '(PDT applies)'}

Should we BUY {symbol} right now? Return JSON:
{{
  "action": "buy|skip",
  "confidence": 0.00,
  "entry_notes": "specific entry condition (e.g. 'wait for pullback to $X')",
  "stop_loss": 0.00,
  "take_profit": 0.00,
  "rationale": "clear reason for decision",
  "risk_flags": ["flag1", "flag2"],
  "expected_hold_time": "minutes|hours|day"
}}

confidence 1.0 = absolute conviction, 0.0 = no edge. Below 0.70 = skip for stocks, below 0.60 = skip for crypto.
stop_loss and take_profit: exact dollar prices, not percentages."""

        try:
            response = self._call_api(
                model=config.FAST_MODEL,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=800,
            )
            data = self._parse_json(response)
            current_price = technical_data.get("current_price", 0)
            return TradeDecision(
                action=data.get("action", "skip"),
                confidence=float(data.get("confidence", 0)),
                rationale=data.get("rationale", ""),
                stop_loss=float(data.get("stop_loss", current_price * (1 - config.STOP_LOSS_PCT))),
                take_profit=float(data.get("take_profit", current_price * (1 + config.TAKE_PROFIT_PCT))),
                risk_flags=data.get("risk_flags", []),
                entry_notes=data.get("entry_notes", ""),
            )
        except Exception as e:
            logger.error(f"Trade decision failed for {symbol}: {e}")
            return TradeDecision(
                action="skip", confidence=0.0,
                rationale=f"AI decision unavailable: {e}",
                stop_loss=0, take_profit=0, risk_flags=["ai_error"],
                entry_notes="",
            )

    def perform_rca(self, trade: dict) -> str:
        """Post-trade root cause analysis. Returns markdown-formatted RCA."""
        pnl = trade.get("pnl", 0)
        outcome = "WIN" if (pnl or 0) > 0 else "LOSS"
        pnl_pct = trade.get("pnl_pct", 0)

        user_message = f"""Post-trade Root Cause Analysis (RCA)

Trade: {trade.get('symbol')} {trade.get('side').upper()} x{trade.get('qty')}
Entry: ${trade.get('entry_price'):.4f} at {trade.get('entry_time')}
Exit: ${trade.get('exit_price', 'N/A')} at {trade.get('exit_time', 'N/A')}
Exit reason: {trade.get('status', 'unknown')}
Strategy: {trade.get('strategy', 'unknown')}
Catalyst: {trade.get('tags', 'unknown')}

OUTCOME: {outcome} — P&L: ${pnl:.2f} ({pnl_pct:.1f}%)

Original rationale: {trade.get('rationale', 'Not recorded')}

Perform a brutally honest RCA. Return JSON:
{{
  "outcome": "win|loss|breakeven",
  "primary_cause": "one sentence: root cause of outcome",
  "what_went_right": ["point1", "point2"],
  "what_went_wrong": ["point1", "point2"],
  "mistake_patterns": ["pattern_tag1", "pattern_tag2"],
  "rule_violated": "name of violated rule, or null",
  "lesson": "actionable lesson for future trades",
  "add_to_rules": "new rule to add if this reveals a gap, or null",
  "conviction_assessment": "was confidence level appropriate? yes|overconfident|underconfident"
}}

mistake_patterns should be short snake_case tags like: chased_gap, ignored_stop_loss, stale_news_play, fomo_entry, wrong_direction_on_vix_spike."""

        try:
            response = self._call_api(
                model=config.ANALYSIS_MODEL,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1200,
            )
            data = self._parse_json(response)

            # Extract learnings and update the DB
            lesson = data.get("lesson", "")
            new_rule = data.get("add_to_rules")
            mistake_patterns = data.get("mistake_patterns", [])

            from trader.journal import Journal
            if new_rule:
                self.journal.add_learning("rules", new_rule)
            for pattern in mistake_patterns:
                self.journal.add_learning("mistakes", pattern)
            if outcome == "WIN" and data.get("what_went_right"):
                for w in data["what_went_right"]:
                    self.journal.add_learning("winning_patterns", w)

            # Format readable RCA
            rca_lines = [
                f"## RCA: {trade.get('symbol')} — {outcome}",
                f"**P&L:** ${pnl:.2f} ({pnl_pct:.1f}%)",
                f"**Root Cause:** {data.get('primary_cause', '')}",
                f"**Lesson:** {lesson}",
            ]
            if mistake_patterns:
                rca_lines.append(f"**Mistake Tags:** {', '.join(mistake_patterns)}")
            if new_rule:
                rca_lines.append(f"**New Rule Added:** {new_rule}")
            return "\n".join(rca_lines)

        except Exception as e:
            logger.error(f"RCA failed for trade {trade.get('id')}: {e}")
            return f"RCA failed: {e}"

    def generate_daily_reflection(self, daily_stats: dict, market_context: str) -> str:
        """EOD journal entry: narrative reflection on the day."""
        user_message = f"""End of day journal entry.

Today's stats: {json.dumps(daily_stats, indent=2)}
Market context: {market_context}

Write a 3-5 sentence trading journal entry covering: what worked, what didn't, what to do differently tomorrow. Be specific and actionable. Return plain text, no JSON."""

        try:
            response = self._call_api(
                model=config.FAST_MODEL,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=400,
            )
            return response
        except Exception as e:
            return f"Reflection unavailable: {e}"

    def _call_api(self, model: str, messages: list, max_tokens: int = 1000) -> str:
        """Call Anthropic API with retry logic."""
        import time
        system = self._build_system_prompt()
        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=model,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                )
                return response.content[0].text
            except anthropic.RateLimitError:
                wait = 2 ** attempt
                logger.warning(f"Rate limit hit, waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
            except anthropic.APIError as e:
                logger.error(f"Anthropic API error: {e}")
                raise
        raise RuntimeError("Anthropic API failed after 3 attempts")

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from Claude response, handling markdown code blocks."""
        # Strip markdown code fences if present
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        text = text.rstrip("```").strip()
        return json.loads(text)
