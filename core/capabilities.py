"""What the live probe (`docs/api_probe.json`) says this key can do.

Optional API fields are requested only when the probe lists them: asking the
search endpoint for a field it does not have fails the whole query, so every
gated feature reads its answer here instead of guessing. The offline fixture
never counts.
"""

from __future__ import annotations

import json
from pathlib import Path

from config import ROOT_DIR

PROBE_PATH = Path(ROOT_DIR) / "docs" / "api_probe.json"


def load(path: str | Path | None = None) -> dict | None:
    """The live probe report, or None when absent, offline or unreadable."""
    p = Path(path or PROBE_PATH)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text())
    except ValueError:
        return None
    if not isinstance(doc, dict) or doc.get("offline"):
        return None
    return doc


def capabilities(path: str | Path | None = None) -> dict:
    doc = load(path)
    return (doc or {}).get("capabilities", {}) or {}


def search_node_fields(path: str | Path | None = None) -> set[str]:
    return set(capabilities(path).get("search_node_fields") or [])


def has_search_field(name: str, path: str | Path | None = None) -> bool:
    return name in search_node_fields(path)


def contents_has_activity(path: str | Path | None = None) -> bool:
    return bool(capabilities(path).get("contents_has_activity"))


def detail_has_activity(path: str | Path | None = None) -> bool:
    """`marketplaceJobPosting(id)` is reachable and carries `activityStat`."""
    return bool(capabilities(path).get("detail_has_activity"))


def detail_client_identity(path: str | Path | None = None) -> bool:
    """The detail type exposes `clientCompanyPublic` (a stable client id)."""
    return bool(capabilities(path).get("detail_client_identity"))


def vendor_proposals(path: str | Path | None = None) -> bool:
    return bool(capabilities(path).get("vendor_proposals"))


def detail_snapshots_reason(path: str | Path | None = None) -> str | None:
    """None when detail-stage snapshots may run, else why they are gated."""
    doc = load(path)
    if doc is None:
        return "gated: run `make probe` first"
    caps = doc.get("capabilities", {}) or {}
    if not caps.get("detail_query"):
        return "gated: probe says marketplaceJobPosting(id) is not reachable on this key"
    if not caps.get("detail_has_activity"):
        return "gated: probe found no activity fields on the detail type"
    return None
