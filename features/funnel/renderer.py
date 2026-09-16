"""Console rendering for the funnel report."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from core.rich_helpers import make_table
from features.funnel.analyzer import DIMENSIONS, STAGES, FunnelReport, WinRate

console = Console()

_DIM_TITLES = {
    "category": "By category",
    "client_segment": "By client segment",
    "budget_band": "By budget band",
    "experience": "By experience level",
    "hour": "By hour notified",
    "platform": "By platform (title labels or cache)",
    "workflow": "By workflow (title labels or cache)",
}


def fmt_win(w: WinRate) -> str:
    if w.submitted == 0:
        return "[bright_black]—[/bright_black]"
    pct = w.shrunk * 100
    colour = "green" if pct >= 20 else "yellow" if pct >= 10 else "red"
    flag = " [bright_black]n<5[/bright_black]" if w.insufficient else ""
    raw = f" [bright_black]({w.hired}/{w.submitted})[/bright_black]"
    return f"[{colour}]{pct:.0f}%[/{colour}]{raw}{flag}"


def render(report: FunnelReport, days: int) -> None:
    if report.events == 0:
        console.print(
            Panel(
                f"[yellow]No proposal events in the last {days} days.[/yellow]\n"
                "Run [cyan]python main.py ingest[/cyan] to read the proposal agent's logs, then "
                "record outcomes with [cyan]python main.py outcome <job> submitted --bid 45[/cyan].",
                title="Proposal funnel",
                border_style="yellow",
            )
        )
        return
    _headline(report, days)
    _funnel_table(report)
    for dim in DIMENSIONS:
        _segment_table(report, dim)


def _headline(report: FunnelReport, days: int) -> None:
    w = report.win
    cost = f"${report.cost_per_hire_usd:,.2f}" if report.cost_per_hire_usd is not None else "—"
    assumed = (
        f"  [bright_black]({report.connects_assumed} submissions assumed "
        f"{config.DEFAULT_CONNECTS_PER_PROPOSAL} connects)[/bright_black]"
        if report.connects_assumed
        else ""
    )
    med_win = f"${report.med_winning_bid:,.0f}" if report.med_winning_bid else "—"
    med_sub = f"${report.med_submitted_bid:,.0f}" if report.med_submitted_bid else "—"
    srcs = ", ".join(f"{k} {v}" for k, v in sorted(report.sources.items())) or "—"
    body = (
        f"[bold cyan]{report.jobs:,}[/bold cyan] jobs · [bold]{report.events:,}[/bold] events in the "
        f"last [bold]{days}[/bold] days\n\n"
        f"[bright_black]Win rate (hired ÷ submitted, shrunk):[/bright_black] {fmt_win(w)}\n"
        f"[bright_black]Connects spent:[/bright_black] {report.connects_spent:,} "
        f"(≈ ${report.connects_spent * config.CONNECT_PRICE_USD:,.2f}){assumed}\n"
        f"[bright_black]Cost per hire:[/bright_black] {cost}   "
        f"[bright_black]Median bid:[/bright_black] submitted {med_sub} · winning {med_win}\n\n"
        f"[bright_black]Sources: {srcs}  ·  last ingest: "
        f"{(report.last_ingested_at or 'never').replace('T', ' ')[:16]}[/bright_black]"
    )
    console.print(Panel(body, title="Proposal funnel", border_style="cyan", padding=(1, 2)))


def _funnel_table(report: FunnelReport) -> None:
    table = make_table("Funnel  ·  distinct jobs per stage")
    table.add_column("Stage", style="bold white", min_width=12)
    table.add_column("Jobs", justify="right", style="cyan", min_width=7)
    table.add_column("Conv.", justify="right", min_width=7)
    table.add_column("Bar", min_width=24)
    top = max(report.stages.values()) or 1
    prev = None
    for stage in STAGES:
        n = report.stages[stage]
        # A stage fed by another source than the one before it (API submissions vs the
        # agent's drafts) has no meaningful conversion; never print > 100%.
        conv = "—" if prev in (None, 0) or n > prev else f"{n / prev * 100:.1f}%"
        width = round(n / top * 24)
        bar = "[cyan]" + "█" * width + "[/cyan]" if n else "[bright_black]·[/bright_black]"
        table.add_row(stage.title(), f"{n:,}", conv, bar)
        prev = n
    for stage, n in report.side_exits.items():
        table.add_row(
            f"[bright_black]{stage.title()}[/bright_black]",
            f"[bright_black]{n:,}[/bright_black]",
            "",
            "",
        )
    console.print(table)


def _segment_table(report: FunnelReport, dim: str, limit: int = 8) -> None:
    rows = [r for r in report.segments.get(dim, []) if r.notified or r.submitted]
    if not rows:
        return
    table = Table(
        title=_DIM_TITLES.get(dim, dim),
        box=box.SIMPLE_HEAVY,
        title_style="bold white",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column(dim.replace("_", " ").title(), style="bold white", min_width=18)
    table.add_column("Notified", justify="right", style="cyan", min_width=8)
    table.add_column("Drafted", justify="right", min_width=8)
    table.add_column("Submitted", justify="right", min_width=9)
    table.add_column("Hired", justify="right", min_width=6)
    table.add_column("Win rate", justify="right", min_width=14)
    for r in rows[:limit]:
        table.add_row(
            r.value,
            f"{r.notified:,}",
            str(r.drafted),
            str(r.submitted),
            str(r.hired),
            fmt_win(r.win),
        )
    console.print(table)
