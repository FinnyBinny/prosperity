import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Alpaca
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_MODE = os.getenv("ALPACA_MODE", "paper")  # "paper" or "live"
ALPACA_BASE_URL = (
    "https://paper-api.alpaca.markets"
    if ALPACA_MODE == "paper"
    else "https://api.alpaca.markets"
)
# Double-gate: live trading requires BOTH ALPACA_MODE=live AND this confirmation
LIVE_TRADING_CONFIRMED = os.getenv("LIVE_TRADING_CONFIRMED", "no").lower() == "yes"

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANALYSIS_MODEL = "claude-opus-4-8"       # Deep analysis, RCA, strategy
FAST_MODEL = "claude-haiku-4-5-20251001"  # Quick intra-day decisions

# Reddit
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "prosperity-trader/1.0")

# News APIs
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Trading parameters
STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "20.00"))
TARGET_CAPITAL = float(os.getenv("TARGET_CAPITAL", "100.00"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.90"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.03"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.08"))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "3"))
ENABLE_CRYPTO = os.getenv("ENABLE_CRYPTO", "true").lower() == "true"
ENABLE_STOCKS = os.getenv("ENABLE_STOCKS", "true").lower() == "true"

# Crypto symbols to monitor (Alpaca supports these)
CRYPTO_WATCHLIST = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD"]

# Small-cap/volatile stock watchlist (under $10, high volume)
STOCK_WATCHLIST = [
    "SNDL", "CTRM", "NAKD", "AMC", "GME", "BBBY",  # meme/volatile
    "SOXS", "SOXL", "TQQQ", "SQQQ",                   # leveraged ETFs
    "RIVN", "LCID", "NKLA",                             # EV plays
]

# News RSS feeds
NEWS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://finance.yahoo.com/rss/topfinstories",
]

# Subreddits for sentiment analysis
SENTIMENT_SUBREDDITS = ["wallstreetbets", "stocks", "investing", "StockMarket", "Daytrading"]

# Timezone
MARKET_TIMEZONE = "America/New_York"
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0
PRE_MARKET_ANALYSIS_HOUR = 6
PRE_MARKET_ANALYSIS_MINUTE = 0

# Journal
JOURNAL_DB = DATA_DIR / "journal.db"
LEARNINGS_FILE = DATA_DIR / "learnings.json"
