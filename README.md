# Prosperity AI Trader

An autonomous AI day trader that thinks like a Citadel quant analyst — but starts with $20.

**Goal: Turn $20 into $100 (400% return)**

Built on [Alpaca Markets](https://alpaca.markets) + [Claude AI (Anthropic)](https://console.anthropic.com).

---

## How It Works

```
Pre-market (6 AM ET)
  ↓ Pulls financial news, Reddit WSB/r/stocks sentiment, macro signals (VIX, futures)
  ↓ Claude AI analyzes everything → builds ranked watchlist
  ↓ Emails you a pre-market brief

Market hours (9:35 AM – 3:45 PM ET)  [runs every 15 min via cron]
  ↓ Monitors open positions → auto stop-loss / take-profit
  ↓ Evaluates watchlist: technical indicators + fresh news + Claude decision gate
  ↓ Executes only when confidence ≥ 70% (stocks) or 60% (crypto)

End of day (4:00 PM ET)
  ↓ Closes all positions
  ↓ Claude runs root cause analysis on every losing trade
  ↓ Writes learnings to database — NEVER repeats the same mistake twice
  ↓ Emails you a P&L summary with AI reflection

Crypto (24/7, every 30 min)
  ↓ Scans BTC/ETH/SOL/DOGE — no PDT rules, no market hours
  ↓ Same Claude decision gate applies
```

---

## Setup

### 1. Get API Keys

| Service | Link | Cost |
|---|---|---|
| Alpaca Markets | [alpaca.markets](https://alpaca.markets) | Free (paper trading) |
| Anthropic (Claude) | [console.anthropic.com](https://console.anthropic.com) | ~$0.01-0.05/day |
| Reddit | [reddit.com/prefs/apps](https://reddit.com/prefs/apps) | Free |
| NewsAPI | [newsapi.org](https://newsapi.org) | Free tier |
| Finnhub | [finnhub.io](https://finnhub.io) | Free tier |

### 2. Install

```bash
git clone https://github.com/finnybinny/prosperity
cd prosperity
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Start with paper trading** — leave `ALPACA_MODE=paper` until you're confident.

### 4. Initialize

```bash
python scripts/setup.py
```

This validates your API keys, connects to Alpaca, and seeds the trading rules database.

### 5. Run

```bash
# Check your account status
python main.py --phase status

# Run pre-market analysis (morning)
python main.py --phase premarket

# Run active trading (market hours)
python main.py --phase market

# Run crypto trading (any time)
python main.py --phase crypto

# End of day — close all + RCA
python main.py --phase eod

# Auto-detect phase from current time
python main.py
```

---

## Automated Schedule (cron)

Add these to your crontab (`crontab -e`). All times are **US Eastern**:

```cron
# Pre-market analysis — 6:00 AM ET Mon-Fri
0 6 * * 1-5   cd /path/to/prosperity && python main.py --phase premarket >> logs/cron.log 2>&1

# Market hours check — every 15 min, 9:35 AM to 3:45 PM ET
*/15 9-15 * * 1-5  cd /path/to/prosperity && python main.py --phase market >> logs/cron.log 2>&1

# End of day — 4:05 PM ET Mon-Fri
5 16 * * 1-5  cd /path/to/prosperity && python main.py --phase eod >> logs/cron.log 2>&1

# Crypto — every 30 min, 24/7
*/30 * * * *  cd /path/to/prosperity && python main.py --phase crypto >> logs/cron.log 2>&1
```

For UTC timezone servers, adjust times: ET = UTC-5 (winter) / UTC-4 (summer EDT).

---

## Risk Management

| Parameter | Default | Notes |
|---|---|---|
| Max position size | 90% of capital | High because account is tiny |
| Stop loss | 3% (stocks), 4.5% (crypto) | Auto-exits to protect capital |
| Take profit | 8% (stocks), 12% (crypto) | Locks in gains |
| Max daily trades (stocks) | 3 | PDT rule compliance |
| Max open positions | 2 | Diversification with small account |
| Daily loss limit | 15% | Stops trading if daily drawdown exceeded |

**PDT Rule**: Under $25,000 in a margin account = max 3 day trades per 5 rolling days.
The system tracks this automatically. When the limit is reached, it shifts to **crypto only** (no PDT rules).

---

## Live Trading

**Paper trading is the default** — zero risk.

To enable live trading with real money:
1. Fund your Alpaca brokerage account
2. Get your live API keys (different from paper keys)
3. Set in `.env`:
```env
ALPACA_MODE=live
LIVE_TRADING_CONFIRMED=yes
ALPACA_API_KEY=your_LIVE_key
ALPACA_SECRET_KEY=your_LIVE_secret
```

The system requires **both** `ALPACA_MODE=live` AND `LIVE_TRADING_CONFIRMED=yes` — double gate against accidental live trading.

---

## Journal & Learning

Every trade is logged to `data/journal.db` (SQLite):

```bash
# Browse journal
python scripts/review.py --today          # today's trades
python scripts/review.py --history 7      # last 7 days
python scripts/review.py --stats          # win rate, P&L, best strategies
python scripts/review.py --learnings      # active rules + mistake patterns
python scripts/review.py --rca 42         # full RCA for trade #42
```

After each losing trade, Claude performs root cause analysis and:
- Tags the mistake pattern (e.g., `chased_gap`, `stale_news_play`)
- Updates the learnings database
- Injects those learnings into every future AI prompt

The system **never repeats the same mistake twice** because Claude sees its own failure history on every decision.

---

## Strategies

| Strategy | When | Edge |
|---|---|---|
| `momentum_breakout` | Market hours | Volume surge + price above SMA20 + trend confirmation |
| `news_catalyst` | Within 4h of news | Fresh catalyst not yet priced in |
| `crypto_scalp` | 24/7 | Momentum + volume surges on major crypto pairs |

Claude acts as a **decision gate** — it doesn't pick stocks, it approves or rejects pre-screened candidates. This keeps AI costs low and makes decisions high-signal.

---

## Intelligence Sources

- **News**: MarketWatch, Yahoo Finance, CNBC, Reuters RSS feeds + NewsAPI + Finnhub
- **Social**: Reddit (r/wallstreetbets, r/stocks, r/investing, r/Daytrading)
- **Technical**: RSI, MACD, Bollinger Bands, EMA, SMA, ATR, volume analysis
- **Macro**: VIX, SPY/QQQ/IWM sector flows, market regime detection

---

## Architecture

```
main.py                      # Orchestrator + phase dispatcher
├── trader/
│   ├── alpaca_client.py     # Alpaca Markets API (orders, data, positions)
│   ├── brain.py             # Claude AI (analysis, decisions, RCA)
│   ├── market_analyzer.py   # News, sentiment, technical, macro data
│   ├── risk_manager.py      # Position sizing, stop loss, PDT, validation
│   ├── executor.py          # Order execution + position monitoring
│   ├── journal.py           # SQLite trade log + learnings
│   ├── notifier.py          # Rich console output + email alerts
│   └── strategies/
│       ├── momentum.py      # Volume breakout screener
│       ├── news_catalyst.py # News-driven play detector
│       └── crypto_scalp.py  # 24/7 crypto momentum
├── scripts/
│   ├── setup.py             # API validation + DB init
│   └── review.py            # Journal browser CLI
└── data/
    ├── journal.db           # All trades, RCA, daily summaries
    └── learnings.json       # Accumulated trading rules + mistake patterns
```

---

## Disclaimer

This is an educational project. Day trading involves substantial risk of loss. Paper trade first. Never risk money you can't afford to lose. Past performance of AI models does not guarantee future trading results.
