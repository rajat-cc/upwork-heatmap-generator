"""Automation lens entry point (implements core.lens.Lens)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.rule import Rule

from core.lens import run_lens
from core.logging_setup import get_logger
from core.rich_helpers import provenance_footer
from db import count_fetch_runs, get_last_success, init_db
from features.automation import analyzer, exporter, renderer
from features.automation.analyzer import _FTS_TERMS, PLATFORM_LABELS, AutomationReport
from features.n8n import classifier

console = Console()
log = get_logger(__name__)


class AutomationLens:
    name = "automation"

    def __init__(self, platforms: list[str] | None = None) -> None:
        self.platforms = [p for p in (platforms or []) if p in PLATFORM_LABELS]

    def analyze(self, days: int) -> AutomationReport:
        jobs = analyzer.load_jobs(days, self.platforms or None)
        report = analyzer.analyze(jobs, self.platforms)
        classifier.persist(j for j in report.jobs if not j.is_purged)
        return report

    def render(self, report: AutomationReport, days: int) -> None:
        last = get_last_success()
        renderer.render(report, days, last_fetch=last)
        since = datetime.now(UTC) - timedelta(days=days)
        console.print(
            provenance_footer(
                days=days,
                n=report.total_jobs,
                runs=count_fetch_runs(since_iso=since.strftime("%Y-%m-%dT%H:%M:%S")),
                last_fetch_iso=last,
            )
        )

    def export(self, report: AutomationReport, days: int) -> str | None:
        if not report.total_jobs:
            return None
        return exporter.export(report, days, data_as_of=get_last_success() or "never")


def fetch_platforms(platforms: list[str] | None, days: int, limit: int) -> int:
    """Pull the last `days` of posts for each platform's headline search term."""
    from fetcher import GraphQLError, TransientError, fetch_jobs

    total = 0
    for p in platforms or PLATFORM_LABELS:
        term = _FTS_TERMS.get(p)
        if not term:
            continue
        try:
            total += fetch_jobs(
                search_term=term, limit=limit, since_days=days, label=f"platform:{p}"
            )
        except (GraphQLError, TransientError, RuntimeError) as exc:
            log.error("Fetch for %s failed: %s", p, exc)
            console.print(f"  [yellow]{p}: fetch failed:[/yellow] {exc}")
            if isinstance(exc, RuntimeError):
                break  # auth: no point continuing
    return total


def run(
    days: int = 7,
    platforms: list[str] | None = None,
    *,
    fetch: bool = False,
    limit: int = 300,
    export: bool = True,
) -> AutomationReport:
    init_db()
    if fetch:
        console.print(Rule("[bold cyan]Fetching automation jobs from Upwork[/bold cyan]"))
        n = fetch_platforms(platforms, days, limit)
        console.print(f"  [green]Fetched / refreshed[/green] {n} jobs from API.\n")
    console.print(Rule(f"[bold cyan]Automation Demand · Last {days} days[/bold cyan]"))
    return run_lens(AutomationLens(platforms), days, export=export)
