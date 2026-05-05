"""Shared building blocks used across features.

- `models`        — typed dataclasses for the domain (Job, Report, …)
- `logging_setup` — lazy initialiser for stdlib `logging` with Rich handler
- `rich_helpers`  — Table factory + cell formatters used by every renderer
- `xlsx_helpers`  — header builder + style palette used by every exporter
"""
