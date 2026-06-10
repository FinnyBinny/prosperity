#!/usr/bin/env python3
"""
One-time setup script: validates API keys, initializes database,
and seeds the learnings table with proven trading rules.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from trader.journal import Journal


INITIAL_RULES = [
    "Never chase a stock that has already gapped up >15% at market open — the move is priced in",
    "Do not trade during the first 5 minutes of market open (9:30-9:35 AM ET) — too volatile",
    "Always set stop loss before entry — non-negotiable, no exceptions",
    "News catalyst plays: if the news is >4 hours old, skip — it's already priced in",
    "If down >15% on the day total, stop all trading and reassess — protect capital",
    "Crypto: only enter during volume surges (>1.5x average) — low volume = trap",
    "PDT limit (stocks): max 3 day trades in any rolling 5-day period — track carefully",
    "After 2 consecutive losing trades, reduce position size by 50% for next trade",
    "Take partial profits when up 5% on a trade — secure some gains",
    "Never average down on a losing position — if it stops out, it stops out",
]

INITIAL_WINNING_PATTERNS = [
    "Momentum stocks: volume surge >3x average + price above SMA20 = high probability",
    "News catalyst within 1 hour: move into the stock before retail catches on",
    "Crypto: BTC breakout above key resistance often drags ETH and altcoins 30-60 min later",
]


def main():
    print("=" * 60)
    print("  PROSPERITY AI TRADER — SETUP")
    print("=" * 60)

    # 1. Check API keys
    print("\n[1] Checking API keys...")
    checks = [
        ("ALPACA_API_KEY", config.ALPACA_API_KEY),
        ("ALPACA_SECRET_KEY", config.ALPACA_SECRET_KEY),
        ("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY),
    ]
    all_good = True
    for name, value in checks:
        if value and len(value) > 10:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} — MISSING")
            all_good = False

    optional_checks = [
        ("REDDIT_CLIENT_ID", config.REDDIT_CLIENT_ID),
        ("FINNHUB_API_KEY", config.FINNHUB_API_KEY),
        ("NEWS_API_KEY", config.NEWS_API_KEY),
    ]
    for name, value in optional_checks:
        if value:
            print(f"  ✓ {name} (optional)")
        else:
            print(f"  ○ {name} (optional — skipped)")

    if not all_good:
        print("\nERROR: Required API keys missing. Copy .env.example to .env and fill in your keys.")
        sys.exit(1)

    # 2. Trading mode
    print(f"\n[2] Trading mode: {config.ALPACA_MODE.upper()}")
    if config.ALPACA_MODE == "live":
        if config.LIVE_TRADING_CONFIRMED:
            print("  *** LIVE TRADING ENABLED — REAL MONEY AT RISK ***")
        else:
            print("  WARNING: ALPACA_MODE=live but LIVE_TRADING_CONFIRMED not set.")
            print("  Add LIVE_TRADING_CONFIRMED=yes to .env to enable live trades.")
    else:
        print("  Paper trading — safe to test, no real money")

    # 3. Initialize database
    print(f"\n[3] Initializing database at {config.JOURNAL_DB}...")
    journal = Journal()
    print(f"  ✓ Database initialized")

    # 4. Seed learnings
    print("\n[4] Seeding trading rules and learnings...")
    for rule in INITIAL_RULES:
        journal.add_learning("rules", rule)
    for pattern in INITIAL_WINNING_PATTERNS:
        journal.add_learning("winning_patterns", pattern)
    learnings = journal.load_learnings()
    print(f"  ✓ {len(learnings['rules'])} rules loaded")
    print(f"  ✓ {len(learnings['winning_patterns'])} winning patterns loaded")

    # 5. Test Alpaca connection
    print("\n[5] Testing Alpaca connection...")
    try:
        from trader.alpaca_client import AlpacaClient
        alpaca = AlpacaClient()
        account = alpaca.get_account()
        print(f"  ✓ Connected — Portfolio: ${account['portfolio_value']:.2f}")
        print(f"    Cash: ${account['cash']:.2f}")
        print(f"    Buying power: ${account['buying_power']:.2f}")
        print(f"    PDT trades used: {account.get('daytrade_count', 0)}")
    except Exception as e:
        print(f"  ✗ Alpaca connection failed: {e}")
        print("    Check your API keys and ensure they match your trading mode (paper/live)")

    # 6. Test Anthropic connection
    print("\n[6] Testing Claude AI connection...")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=config.FAST_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": "Reply with just: OK"}],
        )
        print(f"  ✓ Claude connected ({config.FAST_MODEL})")
    except Exception as e:
        print(f"  ✗ Claude connection failed: {e}")

    print("\n" + "=" * 60)
    print("  SETUP COMPLETE")
    print("=" * 60)
    print(f"\n  Target: ${config.STARTING_CAPITAL:.2f} → ${config.TARGET_CAPITAL:.2f}")
    print(f"  Crypto: {'enabled' if config.ENABLE_CRYPTO else 'disabled'}")
    print(f"  Stocks: {'enabled' if config.ENABLE_STOCKS else 'disabled'}")
    print(f"\n  Run the trader:")
    print(f"    python main.py --phase status     # check account")
    print(f"    python main.py --phase premarket  # morning analysis")
    print(f"    python main.py --phase market     # live trading")
    print(f"    python main.py --phase crypto     # crypto 24/7")
    print(f"    python main.py                    # auto-dispatch by time")
    print()


if __name__ == "__main__":
    main()
