"""Public entry point for the n8n feature.

Called from `main.py::cmd_n8n`. Encapsulates the fetch → analyze →
render → export → persist pipeline.
"""

from __future__ import annotations

from rich.console import Console
from rich.rule import Rule

from core.logging_setup import get_logger
from db import get_last_success, init_db
from features.n8n import analyzer, classifier, exporter, renderer

console = Console()
log = get_logger(__name__)


def run(days: int = 7, fetch: bool = True, limit: int = 1000) -> None:
    """Top-level orchestration."""
    init_db()

    if fetch:
        from fetcher import GraphQLError, TransientError, fetch_jobs

        console.print(Rule("[bold cyan]Fetching n8n jobs from Upwork[/bold cyan]"))
        try:
            n = fetch_jobs(search_term="n8n", limit=limit, since_days=days)
            console.print(f"  [green]Fetched / refreshed[/green] {n} jobs from API.\n")
        except (GraphQLError, TransientError, RuntimeError) as exc:
            log.error("n8n fetch failed: %s", exc)
            console.print(
                f"  [yellow]Fetch failed:[/yellow] {exc}\n  [dim]Analyzing local data only.[/dim]\n"
            )

    console.print(Rule(f"[bold cyan]n8n Demand · Last {days} days[/bold cyan]"))
    last_fetch = get_last_success()
    jobs = analyzer.load_n8n_jobs(days=days)
    report = analyzer.analyze(jobs)
    classifier.persist(j for j in report.jobs if not j.is_purged)
    renderer.render(report, days=days, last_fetch=last_fetch)

    try:
        path = exporter.export(report, days=days, data_as_of=last_fetch or "never")
        console.print(f"\n  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
    except Exception as exc:
        log.exception("Excel export failed")
        console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")
