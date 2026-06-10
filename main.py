#!/usr/bin/env python3
"""
Prosperity AI Trader — Main Orchestrator

Runs in three phases based on market time (or --phase flag):
  premarket  : 6:00-9:30 AM ET — intelligence gathering + watchlist building
  market     : 9:35 AM-3:45 PM ET — active trading + position monitoring
  crypto     : any time — 24/7 crypto scalping
  eod        : 4:00 PM ET — close positions + journal + RCA

Usage:
  python main.py                    # auto-dispatch based on current time
  python main.py --phase premarket
  python main.py --phase market
  python main.py --phase crypto
  python main.py --phase eod
  python main.py --phase status     # just print account status + journal

Cron setup (all times ET):
  0 6  * * 1-5   cd /path/to/prosperity && python main.py --phase premarket
  */15 9-15 * * 1-5  cd /path/to/prosperity && python main.py --phase market
  5 16 * * 1-5   cd /path/to/prosperity && python main.py --phase eod
  */30 * * * *   cd /path/to/prosperity && python main.py --phase crypto
"""
import argparse
import logging
import sys
from datetime import datetime

import pytz

import config
from trader.alpaca_client import AlpacaClient
from trader.market_analyzer import MarketAnalyzer
from trader.brain import ClaudeBrain
from trader.risk_manager import RiskManager, TradeSignal
from trader.executor import TradeExecutor
from trader.journal import Journal
from trader.notifier import Notifier
from trader.strategies.momentum import MomentumStrategy
from trader.strategies.news_catalyst import NewsCatalystStrategy
from trader.strategies.crypto_scalp import CryptoScalpStrategy

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            config.LOGS_DIR / f"trader_{datetime.now().strftime('%Y-%m-%d')}.log"
        ),
    ],
)
logger = logging.getLogger(__name__)


def build_components():
    """Initialize all system components."""
    logger.info("Initializing Prosperity AI Trader...")
    alpaca = AlpacaClient()
    journal = Journal()
    risk = RiskManager(journal)
    brain = ClaudeBrain(journal)
    executor = TradeExecutor(alpaca, risk, journal)
    analyzer = MarketAnalyzer()
    notifier = Notifier()
    return alpaca, journal, risk, brain, executor, analyzer, notifier


def run_premarket(alpaca, journal, risk, brain, executor, analyzer, notifier):
    """
    Pre-market phase (6:00-9:30 AM ET):
    - Pull all market intelligence
    - Run AI analysis to build watchlist
    - No trading yet
    """
    logger.info("=== PHASE: PRE-MARKET ===")

    account = alpaca.get_account()
    risk.set_daily_start_value(account["portfolio_value"])
    notifier.print_startup_banner(account)

    # Gather market intelligence
    logger.info("Gathering market intelligence...")
    brief = analyzer.build_market_brief()

    # Run AI pre-market analysis
    logger.info("Running Claude pre-market analysis...")
    analysis = brain.analyze_premarket(brief)
    notifier.print_premarket_brief(analysis)

    # Store watchlist result for market phase
    _save_watchlist(analysis, brief)

    # Send email brief
    watchlist_str = ", ".join(
        f"{w['symbol']} ({w['catalyst']})" for w in analysis.watchlist
    )
    notifier.send_email(
        f"Pre-Market Brief — {analysis.market_bias.upper()}",
        f"Bias: {analysis.market_bias}\n\n"
        f"{analysis.macro_summary}\n\n"
        f"Watchlist: {watchlist_str or 'FLAT — no trades today'}\n\n"
        f"Risks: {'; '.join(analysis.key_risks)}",
    )

    logger.info("Pre-market analysis complete.")


def run_market(alpaca, journal, risk, brain, executor, analyzer, notifier):
    """
    Market hours phase (9:35 AM - 3:45 PM ET):
    - Monitor open positions (stop loss / take profit)
    - Evaluate watchlist candidates and execute when conviction is high
    """
    logger.info("=== PHASE: MARKET HOURS ===")

    if not alpaca.is_market_open():
        logger.info("Market is closed — skipping market phase")
        return

    account = alpaca.get_account()
    notifier.print_startup_banner(account)
    risk.set_daily_start_value(account["portfolio_value"])

    # 1. Monitor and potentially close existing positions
    closed = executor.monitor_positions(brain)
    for c in closed:
        notifier.print_position_closed(c["symbol"], c.get("pnl", 0), c["reason"])

    # 2. Check if we can take new trades
    account = alpaca.get_account()  # Refresh after any closes
    buying_power = account["buying_power"]
    if buying_power < 1.00:
        logger.info(f"Buying power too low (${buying_power:.2f}) — skipping new trades")
        return

    pdt_count = journal.count_day_trades_this_week()
    pdt_remaining = max(0, config.MAX_DAILY_TRADES - pdt_count)
    open_positions = alpaca.get_positions()
    todays_trades = journal.get_todays_trades()
    trades_today = len([t for t in todays_trades if t["status"] != "open"])

    logger.info(
        f"Account state: ${buying_power:.2f} buying power | "
        f"{pdt_remaining} PDT trades remaining | "
        f"{len(open_positions)} open positions | "
        f"{trades_today} trades today"
    )

    if len(open_positions) >= risk.MAX_OPEN_POSITIONS:
        logger.info("Max open positions reached — monitoring only")
        return

    # 3. Load today's watchlist
    watchlist = _load_watchlist()
    if not watchlist:
        logger.info("No watchlist found — running quick scan")
        watchlist = _quick_scan(analyzer, pdt_remaining)

    # 4. Evaluate and trade
    news = analyzer.fetch_news(15)
    macro = analyzer.get_market_overview()

    for candidate in watchlist[:3]:  # Max 3 evaluations per run
        symbol = candidate.get("symbol", "")
        asset_class = candidate.get("asset_class", "stock")
        catalyst = candidate.get("catalyst", "technical")

        if not symbol:
            continue

        # Skip if PDT would be violated for stocks
        if asset_class == "stock" and pdt_remaining <= 0:
            logger.info(f"Skipping {symbol} (stock) — PDT limit reached")
            continue

        # Get technical data
        technicals = analyzer.get_technical_data(symbol, from_alpaca=alpaca)
        if not technicals:
            continue

        entry_price = technicals.get("current_price", 0)
        if entry_price <= 0:
            continue

        # Get symbol-specific news
        symbol_news = analyzer.get_symbol_news(symbol)

        # Ask Claude to make the trade decision
        decision = brain.make_trade_decision(
            symbol=symbol,
            asset_class=asset_class,
            technical_data=technicals,
            news=symbol_news or news[:5],
            sentiment={"source": "reddit", "trending": False},
            macro=macro,
            pdt_remaining=pdt_remaining,
            buying_power=buying_power,
        )

        logger.info(
            f"Decision for {symbol}: {decision.action.upper()} "
            f"(confidence={decision.confidence:.2f})"
        )

        if decision.action != "buy":
            notifier.print_trade_rejected(symbol, f"AI skip: {decision.rationale[:60]}")
            continue

        # Build signal and execute
        signal = TradeSignal(
            symbol=symbol,
            action="buy",
            asset_class=asset_class,
            confidence=decision.confidence,
            rationale=decision.rationale,
            catalyst=catalyst,
            strategy=candidate.get("strategy", "ai_decision"),
            entry_price=entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            risk_flags=decision.risk_flags,
        )

        result = executor.execute_signal(signal)
        if result:
            notifier.print_trade_executed(result, signal)
            pdt_remaining -= 1 if asset_class == "stock" else 0
            buying_power = alpaca.get_account()["buying_power"]
        else:
            notifier.print_trade_rejected(symbol, "Execution rejected by risk manager")


def run_crypto(alpaca, journal, risk, brain, executor, analyzer, notifier):
    """
    Crypto phase — runs 24/7, no market hours restriction.
    Lower confidence threshold, fractional shares, no PDT.
    """
    logger.info("=== PHASE: CRYPTO ===")

    if not config.ENABLE_CRYPTO:
        logger.info("Crypto trading disabled in config")
        return

    account = alpaca.get_account()
    buying_power = account["buying_power"]
    if buying_power < 1.00:
        logger.info(f"Buying power too low (${buying_power:.2f})")
        return

    # Monitor existing crypto positions
    closed = executor.monitor_positions(brain)
    for c in closed:
        notifier.print_position_closed(c["symbol"], c.get("pnl", 0), c["reason"])

    open_positions = alpaca.get_positions()
    if len(open_positions) >= risk.MAX_OPEN_POSITIONS:
        logger.info("Max positions — monitoring only")
        return

    strategy = CryptoScalpStrategy()
    candidates = strategy.scan_candidates()
    macro = analyzer.get_market_overview()
    news = analyzer.fetch_news(10)
    account = alpaca.get_account()
    buying_power = account["buying_power"]

    for symbol in candidates:
        technicals = analyzer.get_technical_data(symbol, from_alpaca=alpaca)
        if not technicals:
            continue

        entry_price = technicals.get("current_price", 0)
        if entry_price <= 0:
            continue

        decision = brain.make_trade_decision(
            symbol=symbol,
            asset_class="crypto",
            technical_data=technicals,
            news=news[:3],
            sentiment={},
            macro=macro,
            pdt_remaining=99,  # No PDT for crypto
            buying_power=buying_power,
        )

        if decision.action != "buy":
            continue

        signal = TradeSignal(
            symbol=symbol,
            action="buy",
            asset_class="crypto",
            confidence=decision.confidence,
            rationale=decision.rationale,
            catalyst="technical",
            strategy=strategy.name,
            entry_price=entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            risk_flags=decision.risk_flags,
        )
        result = executor.execute_signal(signal)
        if result:
            notifier.print_trade_executed(result, signal)
            break  # One crypto trade per run max


def run_eod(alpaca, journal, risk, brain, executor, analyzer, notifier):
    """
    End of day (4:00 PM ET):
    - Close all open positions
    - Run RCA on losing trades
    - Write daily journal entry
    - Send EOD report email
    """
    logger.info("=== PHASE: END OF DAY ===")

    executor.close_all_eod(brain)

    account = alpaca.get_account()
    stats = journal.get_stats()
    todays_trades = journal.get_todays_trades()

    today_pnl = sum(t.get("pnl", 0) or 0 for t in todays_trades if t.get("status") != "open")
    today_stats = {
        "total_trades": len(todays_trades),
        "wins": sum(1 for t in todays_trades if (t.get("pnl") or 0) > 0),
        "losses": sum(1 for t in todays_trades if (t.get("pnl") or 0) < 0),
        "total_pnl": round(today_pnl, 2),
    }

    notifier.print_eod_summary(today_stats, account["portfolio_value"])

    # AI daily reflection
    macro = analyzer.get_market_overview()
    macro_str = ", ".join(
        f"{k.upper()}: {v.get('price', 0)} ({v.get('change_pct', 0):+.1f}%)"
        for k, v in macro.items()
    )
    reflection = brain.generate_daily_reflection(today_stats, macro_str)
    logger.info(f"Daily reflection:\n{reflection}")

    journal.save_daily_summary({
        **today_stats,
        "portfolio_value": account["portfolio_value"],
        "market_conditions": macro_str,
        "ai_reflection": reflection,
    })

    # Print full performance report
    print(journal.format_performance_report())

    # Email EOD summary
    notifier.send_email(
        f"EOD Summary — P&L: ${today_pnl:+.2f}",
        f"Today's trades: {today_stats['total_trades']} "
        f"({today_stats['wins']}W / {today_stats['losses']}L)\n"
        f"P&L: ${today_pnl:+.2f}\n"
        f"Portfolio: ${account['portfolio_value']:.2f}\n\n"
        f"Reflection:\n{reflection}",
    )


def run_status(alpaca, journal, notifier):
    """Just print current status."""
    account = alpaca.get_account()
    notifier.print_startup_banner(account)
    print(journal.format_performance_report())
    positions = alpaca.get_positions()
    if positions:
        print("\nOpen Positions:")
        for p in positions:
            pnl_color = "+" if float(p["unrealized_pl"]) >= 0 else ""
            print(
                f"  {p['symbol']:8s} {p['side']:5s} x{p['qty']:.4f} "
                f"@ ${p['avg_entry_price']:.4f} → ${p['current_price']:.4f} "
                f"| P&L: {pnl_color}${p['unrealized_pl']:.2f}"
            )


def _save_watchlist(analysis, brief: dict):
    """Persist today's watchlist to data dir for market phase to use."""
    import json
    watchlist_path = config.DATA_DIR / "today_watchlist.json"
    watchlist_path.write_text(json.dumps({
        "date": datetime.now().date().isoformat(),
        "watchlist": analysis.watchlist,
        "market_bias": analysis.market_bias,
        "strategy": analysis.recommended_strategy,
    }, indent=2))


def _load_watchlist() -> list[dict]:
    """Load today's watchlist if it exists."""
    import json
    from datetime import date
    watchlist_path = config.DATA_DIR / "today_watchlist.json"
    if not watchlist_path.exists():
        return []
    try:
        data = json.loads(watchlist_path.read_text())
        if data.get("date") != date.today().isoformat():
            return []  # Stale
        return data.get("watchlist", [])
    except Exception:
        return []


def _quick_scan(analyzer: MarketAnalyzer, pdt_remaining: int) -> list[dict]:
    """Quick fallback scan if no pre-market watchlist exists."""
    candidates = []
    movers = analyzer.scan_for_movers()
    for m in movers[:5]:
        asset_class = "crypto" if "/" in m["symbol"] else "stock"
        if asset_class == "stock" and pdt_remaining <= 0:
            continue
        candidates.append({
            "symbol": m["symbol"],
            "asset_class": asset_class,
            "catalyst": "momentum",
            "rationale": f"{m['change_pct']:+.1f}% move, {m['vol_ratio']:.1f}x volume",
            "strategy": "momentum_breakout",
        })
    return candidates


def auto_dispatch(alpaca, journal, risk, brain, executor, analyzer, notifier):
    """Determine current phase from market time and dispatch."""
    et_tz = pytz.timezone(config.MARKET_TIMEZONE)
    now = datetime.now(et_tz)
    hour, minute = now.hour, now.minute
    is_weekday = now.weekday() < 5  # Monday-Friday

    logger.info(f"Auto-dispatch: {now.strftime('%A %H:%M')} ET")

    if not is_weekday:
        logger.info("Weekend — running crypto phase only")
        run_crypto(alpaca, journal, risk, brain, executor, analyzer, notifier)
        return

    if 6 <= hour < 9 or (hour == 9 and minute < 30):
        run_premarket(alpaca, journal, risk, brain, executor, analyzer, notifier)
    elif (hour == 9 and minute >= 35) or (10 <= hour < 15) or (hour == 15 and minute <= 45):
        run_market(alpaca, journal, risk, brain, executor, analyzer, notifier)
        if config.ENABLE_CRYPTO:
            run_crypto(alpaca, journal, risk, brain, executor, analyzer, notifier)
    elif hour == 16 or (hour == 15 and minute > 45):
        run_eod(alpaca, journal, risk, brain, executor, analyzer, notifier)
    else:
        logger.info("Outside trading hours — running crypto check")
        if config.ENABLE_CRYPTO:
            run_crypto(alpaca, journal, risk, brain, executor, analyzer, notifier)


def main():
    parser = argparse.ArgumentParser(description="Prosperity AI Trader")
    parser.add_argument(
        "--phase",
        choices=["premarket", "market", "crypto", "eod", "status", "auto"],
        default="auto",
        help="Which phase to run (default: auto-detect from time)",
    )
    args = parser.parse_args()

    # Validate config
    missing = []
    if not config.ALPACA_API_KEY:
        missing.append("ALPACA_API_KEY")
    if not config.ALPACA_SECRET_KEY:
        missing.append("ALPACA_SECRET_KEY")
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your API keys.")
        sys.exit(1)

    alpaca, journal, risk, brain, executor, analyzer, notifier = build_components()

    phase_map = {
        "premarket": lambda: run_premarket(alpaca, journal, risk, brain, executor, analyzer, notifier),
        "market": lambda: run_market(alpaca, journal, risk, brain, executor, analyzer, notifier),
        "crypto": lambda: run_crypto(alpaca, journal, risk, brain, executor, analyzer, notifier),
        "eod": lambda: run_eod(alpaca, journal, risk, brain, executor, analyzer, notifier),
        "status": lambda: run_status(alpaca, journal, notifier),
        "auto": lambda: auto_dispatch(alpaca, journal, risk, brain, executor, analyzer, notifier),
    }

    try:
        phase_map[args.phase]()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error in phase '{args.phase}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
