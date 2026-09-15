"""n8n automation demand analysis feature.

Public API:
    run(days: int, fetch: bool = True, limit: int = 1000) -> None

Internally split into:
    classifier — opportunity score + persisted regex tagging
    analyzer   — DB read (FTS5 + strict regex re-validate) + aggregation
    renderer   — Rich console output (5 tables)
    exporter   — 6-sheet Excel workbook
"""

from features.n8n.api import run

__all__ = ["run"]
