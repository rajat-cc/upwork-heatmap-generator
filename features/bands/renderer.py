"""Console rendering for bid bands."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from core.rich_helpers import fmt_band, make_table
from features.bands.analyzer import ALL, BandsReport

console = Console()


def render(report: BandsReport, days: int) -> None:
    if not report.rows:
        console.print(
            Panel(
                f"[yellow]No priced jobs in the last {days} days (or fewer than {report.min_n} per band).[/yellow]\n"
                "Run [cyan]make sync[/cyan] first.",
                title="Bid bands",
                border_style="yellow",
            )
        )
        return

    table = make_table(f"Bid bands  ·  median (p25–p75)  ·  cells need ≥ {report.min_n} jobs")
    table.add_column("Workflow", style="bold white", min_width=22)
    table.add_column("Segment", min_width=9)
    table.add_column("Exp.", min_width=12)
    table.add_column("Type", min_width=6)
    table.add_column("n", justify="right", style="cyan", min_width=5)
    table.add_column("Band", justify="right", style="green", min_width=20)
    for r in report.rows[:40]:
        suffix = "/hr" if r.budget_type == "HOURLY" else ""
        style = "bold" if r.level == "budget_type" else ""
        table.add_row(
            f"[{style}]{'ALL' if r.workflow == ALL else r.workflow}[/{style}]"
            if style
            else r.workflow,
            "—" if r.client_segment == ALL else r.client_segment,
            "—" if r.experience == ALL else r.experience,
            r.budget_type.lower(),
            str(r.n),
            fmt_band(r.p25, r.p50, r.p75, suffix),
        )
    console.print(table)

    if not report.bids:
        console.print(
            "  [bright_black]No submitted bids with amounts in this window yet — record them with `main.py outcome <job> submitted --bid …`.[/bright_black]\n"
        )
        return

    bids = make_table("Your bids vs the band")
    bids.add_column("When", min_width=10)
    bids.add_column("Job", min_width=20)
    bids.add_column("Workflow", min_width=18)
    bids.add_column("Bid", justify="right", style="yellow", min_width=10)
    bids.add_column("Band (level)", justify="right", min_width=24)
    bids.add_column("Position", min_width=8)
    bids.add_column("Outcome", min_width=8)
    for b in report.bids[:30]:
        suffix = "/hr" if b.bid_type == "HOURLY" else ""
        band = (
            fmt_band(b.band_p25 or 0, b.band_p50 or 0, b.band_p75 or 0, suffix)
            if b.band_p50
            else "—"
        )
        pos = {
            "below": "[red]below[/red]",
            "in": "[green]in band[/green]",
            "above": "[yellow]above[/yellow]",
        }.get(b.position, "[bright_black]no band[/bright_black]")
        outcome = {"hired": "[green]hired[/green]", "lost": "[red]lost[/red]"}.get(
            b.outcome, "[bright_black]pending[/bright_black]"
        )
        bids.add_row(
            b.ts[:10],
            b.job_id,
            b.workflow,
            f"${b.bid_amount:,.0f}{suffix}",
            f"{band} [bright_black]{b.band_level}[/bright_black]" if b.band_p50 else "—",
            pos,
            outcome,
        )
    console.print(bids)
