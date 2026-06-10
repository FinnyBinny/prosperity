"""
Trade journal: records every trade, tracks P&L, and drives root cause analysis.
Learnings from past mistakes are persisted and injected into future AI prompts.
"""
import json
import sqlite3
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

import config

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str           # "buy" or "sell"
    qty: float
    entry_price: float
    exit_price: Optional[float]
    entry_time: str
    exit_time: Optional[str]
    strategy: str
    rationale: str
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str = "open"  # "open", "closed", "stopped_out", "target_hit"
    rca: Optional[str] = None
    tags: str = ""          # comma-separated tags
    alpaca_order_id: Optional[str] = None


class Journal:
    def __init__(self, db_path: Path = config.JOURNAL_DB):
        self.db_path = db_path
        self.learnings_path = config.LEARNINGS_FILE
        self._init_db()
        self._init_learnings()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    strategy TEXT,
                    rationale TEXT,
                    pnl REAL,
                    pnl_pct REAL,
                    status TEXT DEFAULT 'open',
                    rca TEXT,
                    tags TEXT,
                    alpaca_order_id TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT UNIQUE NOT NULL,
                    total_trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    total_pnl REAL,
                    portfolio_value REAL,
                    market_conditions TEXT,
                    ai_reflection TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()

    def _init_learnings(self):
        if not self.learnings_path.exists():
            self.learnings_path.write_text(json.dumps({
                "mistakes": [],
                "winning_patterns": [],
                "rules": [
                    "Never chase a stock that has already moved >5% — wait for the pullback",
                    "Always set a stop loss before entering any trade",
                    "News catalyst trades: enter within 15 minutes or skip",
                    "Do not trade the first 5 minutes of market open (extreme volatility)",
                    "If down >3% on the day, stop trading and reassess",
                    "Crypto trades: only enter during high-volume periods",
                ]
            }, indent=2))

    def load_learnings(self) -> dict:
        try:
            return json.loads(self.learnings_path.read_text())
        except Exception:
            return {"mistakes": [], "winning_patterns": [], "rules": []}

    def add_learning(self, category: str, insight: str):
        learnings = self.load_learnings()
        if category in learnings:
            if insight not in learnings[category]:
                learnings[category].append(insight)
                self.learnings_path.write_text(json.dumps(learnings, indent=2))
                logger.info(f"New learning added [{category}]: {insight}")

    def record_trade(self, trade: Trade) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO trades (symbol, side, qty, entry_price, exit_price,
                    entry_time, exit_time, strategy, rationale, pnl, pnl_pct,
                    status, rca, tags, alpaca_order_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.symbol, trade.side, trade.qty, trade.entry_price,
                trade.exit_price, trade.entry_time, trade.exit_time,
                trade.strategy, trade.rationale, trade.pnl, trade.pnl_pct,
                trade.status, trade.rca, trade.tags, trade.alpaca_order_id
            ))
            conn.commit()
            return cursor.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, exit_time: str,
                    status: str, rca: str = None):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT entry_price, qty, side FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
            if not row:
                return
            entry_price, qty, side = row
            if side == "buy":
                pnl = (exit_price - entry_price) * qty
            else:
                pnl = (entry_price - exit_price) * qty
            pnl_pct = (pnl / (entry_price * qty)) * 100
            conn.execute("""
                UPDATE trades SET exit_price=?, exit_time=?, status=?, rca=?,
                    pnl=?, pnl_pct=? WHERE id=?
            """, (exit_price, exit_time, status, rca, pnl, pnl_pct, trade_id))
            conn.commit()
        logger.info(f"Trade {trade_id} closed: PnL=${pnl:.2f} ({pnl_pct:.1f}%)")

    def get_open_trades(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='open'"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_todays_trades(self) -> list[dict]:
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE entry_time LIKE ?", (f"{today}%",)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def count_day_trades_this_week(self) -> int:
        """Count PDT-relevant day trades in the last 5 trading days."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM trades
                WHERE entry_time >= ? AND status NOT IN ('open')
                AND exit_time IS NOT NULL
                AND DATE(entry_time) = DATE(exit_time)
            """, (cutoff,)).fetchone()
            return row[0] if row else 0

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl) as total_pnl,
                    AVG(CASE WHEN pnl > 0 THEN pnl_pct END) as avg_win_pct,
                    AVG(CASE WHEN pnl <= 0 THEN pnl_pct END) as avg_loss_pct
                FROM trades WHERE status != 'open'
            """).fetchone()
            total, wins, losses, total_pnl, avg_win, avg_loss = row
            win_rate = (wins / total * 100) if total else 0
            return {
                "total_trades": total or 0,
                "wins": wins or 0,
                "losses": losses or 0,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(total_pnl or 0, 2),
                "avg_win_pct": round(avg_win or 0, 2),
                "avg_loss_pct": round(avg_loss or 0, 2),
            }

    def save_daily_summary(self, summary: dict):
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_summaries
                    (trade_date, total_trades, wins, losses, total_pnl,
                     portfolio_value, market_conditions, ai_reflection, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, summary.get("total_trades"), summary.get("wins"),
                summary.get("losses"), summary.get("total_pnl"),
                summary.get("portfolio_value"), summary.get("market_conditions"),
                summary.get("ai_reflection"), datetime.now().isoformat()
            ))
            conn.commit()

    def format_performance_report(self) -> str:
        stats = self.get_stats()
        recent = self.get_recent_trades(10)
        learnings = self.load_learnings()

        lines = [
            "=" * 60,
            "  PROSPERITY TRADER — PERFORMANCE REPORT",
            "=" * 60,
            f"  Total Trades:  {stats['total_trades']}",
            f"  Win Rate:      {stats['win_rate']}%",
            f"  Wins / Losses: {stats['wins']} / {stats['losses']}",
            f"  Total P&L:     ${stats['total_pnl']:+.2f}",
            f"  Avg Win:       {stats['avg_win_pct']:+.1f}%",
            f"  Avg Loss:      {stats['avg_loss_pct']:+.1f}%",
            "",
            "  RECENT TRADES",
            "-" * 60,
        ]
        for t in recent:
            status_icon = "✓" if (t.get("pnl") or 0) > 0 else "✗"
            pnl_str = f"${t['pnl']:+.2f}" if t.get("pnl") is not None else "OPEN"
            lines.append(
                f"  {status_icon} {t['symbol']:8s} {t['side']:4s} "
                f"x{t['qty']:.2f} @ ${t['entry_price']:.4f} → {pnl_str}"
            )
        lines += [
            "",
            f"  ACTIVE RULES ({len(learnings['rules'])})",
            "-" * 60,
        ]
        for i, rule in enumerate(learnings["rules"][:5], 1):
            lines.append(f"  {i}. {rule}")
        lines.append("=" * 60)
        return "\n".join(lines)
