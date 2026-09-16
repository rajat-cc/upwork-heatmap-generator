"""Public entry points for the general intelligence dashboard."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.rule import Rule

import config
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
    good_segments,
    hourly_matrix,
    shift_recommendation,
    skills_stats,
    winnable_matrix,
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
    winnable = winnable_matrix(tz_name=tz, days=days, segments=good_segments())
    if any(v for row in winnable for v in row):
        render_volume_heatmap(
            winnable,
            tz_name=tz,
            days=days,
            title=f"WINNABLE POSTINGS  ·  ≤ {config.WINNABLE_MAX_APPLICANTS} applicants at first sight, "
            "segments you convert in",
        )
        rec = shift_recommendation(winnable)
        render_shift_recommendation(rec, tz_name=tz, basis="winnable postings")
    else:
        console.print(
            "  [bright_black]Winnable grid: no snapshots yet — run `make sync` a few times.[/bright_black]\n"
        )
        rec = shift_recommendation(matrix)
        render_shift_recommendation(rec, tz_name=tz)
    console.print(_footer(days=days, n=_jobs_in_window(days), last_fetch=last_fetch))


# ─── Helpers ─────────────────────────────────────────────────────────────────


class DashboardLens:
    """The general dashboard on the Lens protocol (analyze → render → export)."""

    name = "dashboard"

    def __init__(self, tz: str, categories: list | None = None) -> None:
        self.tz = tz
        self.categories = categories or None

    def analyze(self, days: int) -> dict:
        matrix = hourly_matrix(tz_name=self.tz, days=days)
        return {
            "skills": skills_stats(days=days, categories=self.categories),
            "clients": client_stats(days=days),
            "matrix": matrix,
            "shift": shift_recommendation(matrix),
            "last_fetch": get_last_success(),
        }

    def render(self, report: dict, days: int) -> None:
        console.print(f"  {data_as_of_line(report['last_fetch'])}\n")
        render_skills_heatmap(report["skills"], days=days, categories=self.categories)
        render_client_intelligence(report["clients"], days=days)
        render_volume_heatmap(report["matrix"], tz_name=self.tz, days=days)
        render_shift_recommendation(report["shift"], tz_name=self.tz)
        console.print(
            _footer(
                days=days, n=report["clients"].get("total_jobs", 0), last_fetch=report["last_fetch"]
            )
        )

    def export(self, report: dict, days: int) -> str | None:
        return export_dashboard(
            report["skills"], report["clients"], report["matrix"], report["shift"],
            tz_name=self.tz, days=days, data_as_of=report["last_fetch"] or "never",
        )  # fmt: skip


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
