#!/usr/bin/env python3
"""
Journal browser CLI — review trades, learnings, and performance stats.

Usage:
  python scripts/review.py --today
  python scripts/review.py --history 7
  python scripts/review.py --stats
  python scripts/review.py --learnings
  python scripts/review.py --rca TRADE_ID
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

from trader.journal import Journal
import config

console = Console()


def show_today(journal: Journal):
    trades = journal.get_todays_trades()
    if not trades:
        console.print("[dim]No trades today[/dim]")
        return
    _print_trade_table(trades, title="Today's Trades")


def show_history(journal: Journal, days: int):
    trades = journal.get_recent_trades(days * 5)
    if not trades:
        console.print("[dim]No trades found[/dim]")
        return
    _print_trade_table(trades, title=f"Recent Trades (last {len(trades)})")


def show_stats(journal: Journal):
    stats = journal.get_stats()
    console.print(journal.format_performance_report())


def show_learnings(journal: Journal):
    learnings = journal.load_learnings()
    console.print("\n[bold cyan]ACTIVE TRADING RULES[/bold cyan]")
    for i, rule in enumerate(learnings.get("rules", []), 1):
        console.print(f"  [dim]{i:2d}.[/dim] {rule}")

    if learnings.get("mistakes"):
        console.print("\n[bold red]KNOWN MISTAKE PATTERNS[/bold red]")
        for m in learnings["mistakes"]:
            console.print(f"  [red]✗[/red] {m}")

    if learnings.get("winning_patterns"):
        console.print("\n[bold green]WINNING PATTERNS[/bold green]")
        for w in learnings["winning_patterns"]:
            console.print(f"  [green]✓[/green] {w}")


def show_rca(journal: Journal, trade_id: int):
    with __import__("sqlite3").connect(journal.db_path) as conn:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        console.print(f"[red]Trade {trade_id} not found[/red]")
        return
    trade = dict(row)
    pnl = trade.get("pnl") or 0
    color = "green" if pnl >= 0 else "red"
    console.print(f"\n[bold {color}]Trade #{trade_id}: {trade['symbol']} {trade['side'].upper()}[/bold {color}]")
    console.print(f"  Entry:  ${trade['entry_price']:.4f} @ {trade['entry_time']}")
    console.print(f"  Exit:   ${trade.get('exit_price', 'OPEN')} @ {trade.get('exit_time', 'N/A')}")
    console.print(f"  P&L:    ${pnl:+.2f} ({trade.get('pnl_pct', 0):+.1f}%)")
    console.print(f"  Status: {trade.get('status', 'unknown')}")
    if trade.get("rationale"):
        console.print(f"\n[dim]Rationale:[/dim] {trade['rationale']}")
    if trade.get("rca"):
        console.print(f"\n[bold]RCA:[/bold]\n{trade['rca']}")
    else:
        console.print("\n[dim]No RCA recorded for this trade[/dim]")


def _print_trade_table(trades: list[dict], title: str):
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="dim", width=4)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Side", width=5)
    table.add_column("Qty", width=8)
    table.add_column("Entry $", width=9)
    table.add_column("Exit $", width=9)
    table.add_column("P&L", width=10)
    table.add_column("Status", width=12)
    table.add_column("Date")

    for t in trades:
        pnl = t.get("pnl")
        pnl_str = f"${pnl:+.2f}" if pnl is not None else "OPEN"
        pnl_style = "green" if (pnl or 0) > 0 else "red" if (pnl or 0) < 0 else "dim"
        table.add_row(
            str(t["id"]),
            t["symbol"],
            t["side"],
            f"{t['qty']:.4f}",
            f"${t['entry_price']:.4f}",
            f"${t['exit_price']:.4f}" if t.get("exit_price") else "-",
            f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
            t.get("status", ""),
            t["entry_time"][:10] if t.get("entry_time") else "",
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Prosperity Journal Browser")
    parser.add_argument("--today", action="store_true", help="Show today's trades")
    parser.add_argument("--history", type=int, metavar="N", help="Show last N days of trades")
    parser.add_argument("--stats", action="store_true", help="Show overall performance stats")
    parser.add_argument("--learnings", action="store_true", help="Show learned rules and patterns")
    parser.add_argument("--rca", type=int, metavar="TRADE_ID", help="Show RCA for a trade")
    args = parser.parse_args()

    journal = Journal()

    if args.today:
        show_today(journal)
    elif args.history:
        show_history(journal, args.history)
    elif args.stats:
        show_stats(journal)
    elif args.learnings:
        show_learnings(journal)
    elif args.rca:
        show_rca(journal, args.rca)
    else:
        # Default: show today + stats
        show_today(journal)
        show_stats(journal)


if __name__ == "__main__":
    main()
