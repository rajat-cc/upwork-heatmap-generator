"""The Lens protocol: analyze → render → export, one object per analysis.

Every lens after Phase 1 implements this so `main.py` can dispatch through a
registry instead of one bespoke `api.py` per feature. The dashboard and n8n
lenses migrate onto it in Phase 4.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from rich.console import Console

from core.logging_setup import get_logger
from db import init_db

console = Console()
log = get_logger(__name__)


@runtime_checkable
class Lens(Protocol):
    name: str

    def analyze(self, days: int) -> Any: ...

    def render(self, report: Any, days: int) -> None: ...

    def export(self, report: Any, days: int) -> str | None: ...


def run_lens(lens: Lens, days: int, *, export: bool = True) -> Any:
    """Standard orchestration: init DB, analyze, render, export (never crash on export)."""
    init_db()
    report = lens.analyze(days)
    lens.render(report, days)
    if export:
        try:
            path = lens.export(report, days)
            if path:
                console.print(f"  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
        except Exception as exc:  # export is a convenience, never the point
            log.exception("%s export failed", lens.name)
            console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")
    return report
