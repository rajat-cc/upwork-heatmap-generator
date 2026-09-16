"""Public entry points for the general intelligence dashboard."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.rule import Rule

from core.logging_setup import get_logger
from core.rich_helpers import data_as_of_line, provenance_footer
from db import (
    count_fetch_runs,
    count_jobs_since,
    get_date_range,
    get_last_success,
    get_total_jobs,
    init_db,
)
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
        last_fetch = get_last_success()

        console.clear()
        console.print(Rule("[bold cyan]UPWORK JOB INTELLIGENCE DASHBOARD[/bold cyan]"))
        console.print(
            f"  [bright_black]{total:,} jobs  ·  "
            f"{(min_date or 'N/A')[:10]} → {(max_date or 'N/A')[:10]}  ·  "
            f"TZ: {tz}[/bright_black]"
        )
        console.print(f"  {data_as_of_line(last_fetch)}\n")

        stats = skills_stats(days=days, categories=categories or None)
        render_skills_heatmap(stats, days=days, categories=categories)

        cs = client_stats(days=days)
        render_client_intelligence(cs, days=days)

        matrix = hourly_matrix(tz_name=tz, days=days)
        render_volume_heatmap(matrix, tz_name=tz, days=days)

        rec = shift_recommendation(matrix)
        render_shift_recommendation(rec, tz_name=tz)

        console.print(_footer(days=days, n=cs.get("total_jobs", 0), last_fetch=last_fetch))
        _export(tz=tz, days=days, categories=categories, data_as_of=last_fetch or "never")

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
    last_fetch = get_last_success()
    console.print(Rule("[bold cyan]Skills Demand Heatmap[/bold cyan]"))
    console.print(f"  {data_as_of_line(last_fetch)}")
    stats = skills_stats(days=days, categories=categories or None)
    render_skills_heatmap(stats, days=days, categories=categories)
    console.print(_footer(days=days, n=_jobs_in_window(days), last_fetch=last_fetch))


def run_shift_only(days: int, tz: str) -> None:
    init_db()
    _require_data()
    last_fetch = get_last_success()
    console.print(Rule(f"[bold cyan]Job Volume Heatmap  ·  {tz}[/bold cyan]"))
    console.print(f"  {data_as_of_line(last_fetch)}")
    matrix = hourly_matrix(tz_name=tz, days=days)
    render_volume_heatmap(matrix, tz_name=tz, days=days)
    rec = shift_recommendation(matrix)
    render_shift_recommendation(rec, tz_name=tz)
    console.print(_footer(days=days, n=_jobs_in_window(days), last_fetch=last_fetch))


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _jobs_in_window(days: int) -> int:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    return count_jobs_since(since)


def _footer(*, days: int, n: int, last_fetch: str | None) -> str:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    return provenance_footer(
        days=days, n=n, runs=count_fetch_runs(since_iso=since), last_fetch_iso=last_fetch
    )


def _require_data() -> None:
    if get_total_jobs() == 0:
        console.print("[yellow]Database is empty.[/yellow]\n")
        console.print("  To use demo data:    [cyan]python main.py seed[/cyan]")
        console.print("  To fetch real data:  [cyan]python main.py fetch[/cyan]\n")
        sys.exit(0)


def _export(tz: str, days: int, categories: list | None = None, data_as_of: str = "") -> None:
    try:
        stats = skills_stats(days=days, categories=categories or None)
        cs = client_stats(days=days)
        matrix = hourly_matrix(tz_name=tz, days=days)
        rec = shift_recommendation(matrix)
        path = export_dashboard(
            stats, cs, matrix, rec, tz_name=tz, days=days, data_as_of=data_as_of
        )
        console.print(f"  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
    except Exception as exc:
        log.exception("Dashboard Excel export failed")
        console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")
