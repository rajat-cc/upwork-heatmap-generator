"""Public entry points for the general intelligence dashboard."""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.rule import Rule

from core.logging_setup import get_logger
from db import get_date_range, get_total_jobs, init_db
from features.dashboard.analyzer import (
    client_stats,
    hourly_matrix,
    shift_recommendation,
    skills_stats,
)
from features.dashboard.exporter import export_dashboard
from features.dashboard.renderer import (
    render_client_intelligence,
    render_shift_recommendation,
    render_skills_heatmap,
    render_volume_heatmap,
)

console = Console()
log = get_logger(__name__)


def run(
    days: int,
    tz: str,
    categories: list | None = None,
    watch: int | None = None,
) -> None:
    """Full dashboard with optional auto-refresh loop."""
    init_db()
    _require_data()

    while True:
        min_date, max_date = get_date_range()
        total = get_total_jobs()

        console.clear()
        console.print(Rule("[bold cyan]UPWORK JOB INTELLIGENCE DASHBOARD[/bold cyan]"))
        console.print(
            f"  [bright_black]{total:,} jobs  ·  "
            f"{(min_date or 'N/A')[:10]} → {(max_date or 'N/A')[:10]}  ·  "
            f"TZ: {tz}[/bright_black]\n"
        )

        stats = skills_stats(days=days, categories=categories or None)
        render_skills_heatmap(stats, days=days, categories=categories)

        cs = client_stats(days=days)
        render_client_intelligence(cs, days=days)

        matrix = hourly_matrix(tz_name=tz, days=days)
        render_volume_heatmap(matrix, tz_name=tz, days=days)

        rec = shift_recommendation(matrix)
        render_shift_recommendation(rec, tz_name=tz)

        _export(tz=tz, days=days, categories=categories)

        if not watch:
            break

        console.print(
            f"  [bright_black]Next refresh in {watch} min  ·  Ctrl+C to exit[/bright_black]\n"
        )
        try:
            time.sleep(watch * 60)
        except KeyboardInterrupt:
            console.print("\n[yellow]Exiting.[/yellow]\n")
            break


def run_skills_only(days: int, categories: list | None = None) -> None:
    init_db()
    _require_data()
    console.print(Rule("[bold cyan]Skills Demand Heatmap[/bold cyan]"))
    stats = skills_stats(days=days, categories=categories or None)
    render_skills_heatmap(stats, days=days, categories=categories)


def run_shift_only(days: int, tz: str) -> None:
    init_db()
    _require_data()
    console.print(Rule(f"[bold cyan]Job Volume Heatmap  ·  {tz}[/bold cyan]"))
    matrix = hourly_matrix(tz_name=tz, days=days)
    render_volume_heatmap(matrix, tz_name=tz, days=days)
    rec = shift_recommendation(matrix)
    render_shift_recommendation(rec, tz_name=tz)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _require_data() -> None:
    if get_total_jobs() == 0:
        console.print("[yellow]Database is empty.[/yellow]\n")
        console.print("  To use demo data:    [cyan]python main.py seed[/cyan]")
        console.print("  To fetch real data:  [cyan]python main.py fetch[/cyan]\n")
        sys.exit(0)


def _export(tz: str, days: int, categories: list | None = None) -> None:
    try:
        stats = skills_stats(days=days, categories=categories or None)
        cs = client_stats(days=days)
        matrix = hourly_matrix(tz_name=tz, days=days)
        rec = shift_recommendation(matrix)
        path = export_dashboard(stats, cs, matrix, rec, tz_name=tz, days=days)
        console.print(f"  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
    except Exception as exc:
        log.exception("Dashboard Excel export failed")
        console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")
