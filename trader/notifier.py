"""
Notification system: rich console output + optional email alerts.
Keeps you informed without requiring constant screen watching.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

import config

logger = logging.getLogger(__name__)
console = Console()


class Notifier:
    def __init__(self):
        self.email_enabled = bool(
            os.getenv("SMTP_HOST") and os.getenv("NOTIFY_EMAIL")
        )

    def print_startup_banner(self, account: dict):
        portfolio = account.get("portfolio_value", 0)
        cash = account.get("cash", 0)
        goal = config.TARGET_CAPITAL
        start = config.STARTING_CAPITAL
        progress_pct = min(100, ((portfolio - start) / (goal - start)) * 100) if portfolio > start else 0

        bar_len = 40
        filled = int(bar_len * progress_pct / 100)
        bar = "[green]" + "█" * filled + "[/green]" + "░" * (bar_len - filled)

        content = (
            f"[bold cyan]PROSPERITY AI TRADER[/bold cyan]  "
            f"[dim]{config.ALPACA_MODE.upper()} MODE[/dim]\n\n"
            f"  Portfolio:    [bold green]${portfolio:.2f}[/bold green]\n"
            f"  Cash:         ${cash:.2f}\n"
            f"  Goal:         ${goal:.2f}\n"
            f"  Progress:     {bar} {progress_pct:.1f}%\n"
            f"  PDT trades:   {account.get('daytrade_count', 0)}/3 used\n"
            f"  Time:         {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}"
        )
        console.print(Panel(content, border_style="cyan", padding=(1, 2)))

    def print_premarket_brief(self, analysis):
        """Display pre-market analysis results."""
        bias_colors = {
            "bullish": "green", "bearish": "red",
            "volatile": "yellow", "neutral": "dim",
        }
        bias_color = bias_colors.get(analysis.market_bias, "white")

        console.print(f"\n[bold]PRE-MARKET BRIEF[/bold]  "
                      f"[{bias_color}]{analysis.market_bias.upper()}[/{bias_color}]")
        console.print(f"[dim]{analysis.macro_summary}[/dim]")

        if analysis.watchlist:
            table = Table(title="Today's Watchlist", box=box.SIMPLE_HEAVY)
            table.add_column("#", style="dim", width=3)
            table.add_column("Symbol", style="bold cyan")
            table.add_column("Class", style="dim")
            table.add_column("Catalyst", style="yellow")
            table.add_column("Rationale")
            for i, item in enumerate(analysis.watchlist, 1):
                table.add_row(
                    str(i),
                    item.get("symbol", ""),
                    item.get("asset_class", ""),
                    item.get("catalyst", ""),
                    item.get("rationale", "")[:60],
                )
            console.print(table)
        else:
            console.print("[yellow]No watchlist — strategy: STAY FLAT[/yellow]")

        if analysis.key_risks:
            console.print("[bold red]Key Risks:[/bold red]")
            for risk in analysis.key_risks:
                console.print(f"  [red]▸[/red] {risk}")

    def print_trade_executed(self, result: dict, signal):
        console.print(
            f"\n[bold green]TRADE EXECUTED[/bold green]  "
            f"[cyan]{signal.symbol}[/cyan]  "
            f"[bold]{signal.action.upper()}[/bold]  "
            f"x{result['qty']:.4f}  "
            f"@ ~${signal.entry_price:.4f}\n"
            f"  [dim]SL:[/dim] [red]${result['stop_loss']:.4f}[/red]  "
            f"[dim]TP:[/dim] [green]${result['take_profit']:.4f}[/green]\n"
            f"  [dim]{signal.rationale[:80]}[/dim]"
        )

    def print_trade_rejected(self, symbol: str, reason: str):
        console.print(f"  [dim]SKIP {symbol}: {reason}[/dim]")

    def print_position_closed(self, symbol: str, pnl: float, reason: str):
        icon = "[green]✓[/green]" if pnl >= 0 else "[red]✗[/red]"
        console.print(
            f"  {icon} CLOSED {symbol}  P&L: [{'green' if pnl>=0 else 'red'}]${pnl:+.2f}[/]  ({reason})"
        )

    def print_eod_summary(self, stats: dict, portfolio_value: float):
        goal = config.TARGET_CAPITAL
        start = config.STARTING_CAPITAL
        progress_pct = min(100, ((portfolio_value - start) / (goal - start)) * 100) if portfolio_value > start else 0
        bar_len = 30
        filled = int(bar_len * progress_pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        win_color = "green" if stats.get("wins", 0) >= stats.get("losses", 0) else "red"
        pnl_color = "green" if stats.get("total_pnl", 0) >= 0 else "red"

        content = (
            f"[bold]END OF DAY SUMMARY[/bold]\n\n"
            f"  Trades today:  {stats.get('total_trades', 0)}\n"
            f"  Win/Loss:      [{win_color}]{stats.get('wins',0)}W / {stats.get('losses',0)}L[/{win_color}]\n"
            f"  P&L:           [{pnl_color}]${stats.get('total_pnl', 0):+.2f}[/{pnl_color}]\n"
            f"  Portfolio:     ${portfolio_value:.2f}\n"
            f"  Goal Progress: {bar} {progress_pct:.1f}%  [${portfolio_value:.2f} / ${goal:.2f}]"
        )
        console.print(Panel(content, border_style="blue"))

    def print_rca(self, symbol: str, pnl: float, rca_text: str):
        color = "green" if pnl >= 0 else "red"
        console.print(f"\n[bold {color}]RCA: {symbol}[/bold {color}]")
        console.print(rca_text)

    def send_email(self, subject: str, body: str):
        if not self.email_enabled:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Prosperity] {subject}"
            msg["From"] = os.getenv("SMTP_USER", "")
            msg["To"] = os.getenv("NOTIFY_EMAIL", "")
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", 587))) as server:
                server.starttls()
                server.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
                server.sendmail(msg["From"], [msg["To"]], msg.as_string())
            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.warning(f"Email failed: {e}")
