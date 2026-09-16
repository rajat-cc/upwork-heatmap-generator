"""Bands lens entry point."""

from __future__ import annotations

from rich.console import Console
from rich.rule import Rule

from core.lens import run_lens
from core.rich_helpers import data_as_of_line, provenance_footer
from core.scoring import get_scoring
from db import count_fetch_runs, get_last_success
from features.bands import analyzer, exporter, renderer
from features.bands.analyzer import BandsReport

console = Console()


class BandsLens:
    name = "bands"

    def analyze(self, days: int) -> BandsReport:
        return analyzer.analyze(days)

    def render(self, report: BandsReport, days: int) -> None:
        last = get_last_success()
        console.print(Rule(f"[bold cyan]Bid Bands · Last {days} days[/bold cyan]"))
        console.print(f"  {data_as_of_line(last)}")
        renderer.render(report, days)
        console.print(
            provenance_footer(
                days=days,
                n=report.jobs,
                runs=count_fetch_runs(since_iso=report.since),
                last_fetch_iso=last,
                extra=f"scoring {get_scoring().version} · min band sample {report.min_n}",
            )
        )

    def export(self, report: BandsReport, days: int) -> str | None:
        return exporter.export(report, days) if report.rows else None


def run(days: int = 30, *, export: bool = True) -> BandsReport:
    return run_lens(BandsLens(), days, export=export)
