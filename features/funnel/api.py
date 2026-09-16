"""Public entry points for the funnel: the lens, ingest and outcome commands."""

from __future__ import annotations

from rich.console import Console
from rich.rule import Rule

from core.lens import run_lens
from core.rich_helpers import provenance_footer
from db import get_last_success, init_db
from features.funnel import analyzer, exporter, renderer
from features.funnel.analyzer import FunnelReport

console = Console()


class FunnelLens:
    name = "funnel"

    def analyze(self, days: int) -> FunnelReport:
        return analyzer.analyze(days)

    def render(self, report: FunnelReport, days: int) -> None:
        console.print(Rule(f"[bold cyan]Proposal Funnel · Last {days} days[/bold cyan]"))
        renderer.render(report, days)
        console.print(
            provenance_footer(
                days=days,
                n=report.jobs,
                runs=0,
                last_fetch_iso=get_last_success(),
                extra=f"{report.events:,} events · last ingest "
                f"{(report.last_ingested_at or 'never').replace('T', ' ')[:16]}",
            )
        )

    def export(self, report: FunnelReport, days: int) -> str | None:
        return exporter.export(report, days) if report.events else None


def run(days: int = 30, *, export: bool = True) -> FunnelReport:
    return run_lens(FunnelLens(), days, export=export)


def run_ingest(agent_dir: str | None = None) -> None:
    from features.funnel.ingest import ingest

    init_db()
    console.print(Rule("[bold cyan]Ingest proposal events[/bold cyan]"))
    result = ingest(agent_dir)
    for name, info in result.sources.items():
        if info.get("missing"):
            console.print(f"  [yellow]{name}:[/yellow] not found at {info['path']}")
        elif info.get("error"):
            console.print(f"  [red]{name}:[/red] {info['error']}")
        else:
            extra = (
                f"  (skipped {info['skipped_rows']} malformed rows)"
                if info.get("skipped_rows")
                else ""
            )
            console.print(
                f"  [bold]{name}:[/bold] {info['seen']:,} events seen, "
                f"[green]{info['inserted']:,} new[/green]{extra}"
            )
    console.print(
        f"\n  [green]Done.[/green] {result.inserted:,} new events from {result.seen:,} seen.\n"
    )
