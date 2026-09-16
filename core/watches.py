"""Watches: which searches `sync` runs.

A watch is a labelled Upwork search. The file format is the proposal agent's
`watches.txt` (one search per line: `Label | <pasted Upwork search URL>`), so
both tools can watch the same population from the same file. Plain lines are
treated as a bare search expression.

Resolution order for the file:
  1. `UPWORK_WATCHES_FILE`
  2. `<UPWORK_AGENT_DIR>/watches.txt`  (read-only; the agent owns it)
  3. `watch_terms.txt` in this repo
  4. a built-in default (`n8n`)

The URL parser is a port of the agent's `watch.parse_upwork_search` limited
to the fields the search API filters on server-side.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from config import AGENT_DIR, ROOT_DIR, WATCHES_FILE

DEFAULT_LABEL = "watch"


@dataclass(slots=True)
class Watch:
    label: str = DEFAULT_LABEL
    search_expression: str | None = None
    category_ids: list[str] = field(default_factory=list)
    subcategory_ids: list[str] = field(default_factory=list)
    skill_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.search_expression:
            bits.append(f'"{self.search_expression}"')
        if self.category_ids:
            bits.append(f"{len(self.category_ids)} cat")
        if self.subcategory_ids:
            bits.append(f"{len(self.subcategory_ids)} subcat")
        if self.skill_ids:
            bits.append(f"{len(self.skill_ids)} skill")
        return ", ".join(bits) or "no filters"


def _multi(params: dict, *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        for value in params.get(key, []):
            out.extend(p.strip() for p in value.split(",") if p.strip())
    return out


def parse_upwork_search(raw: str, label: str | None = None) -> Watch:
    """Turn a pasted Upwork search URL (or query string, or bare term) into a Watch."""
    raw = raw.strip()
    if "?" in raw:
        query = raw.split("?", 1)[1]
    elif "=" in raw and "://" not in raw:
        query = raw
    else:
        # A bare expression such as `n8n automation`.
        return Watch(label=label or raw or DEFAULT_LABEL, search_expression=raw or None)

    params = urllib.parse.parse_qs(query, keep_blank_values=False)
    q = (params.get("q") or [None])[0]
    return Watch(
        label=label or q or DEFAULT_LABEL,
        search_expression=q,
        category_ids=_multi(params, "category2_uid", "category_uid"),
        subcategory_ids=_multi(params, "subcategory2_uid"),
        skill_ids=_multi(params, "ontology_skill_uid", "occupation_uid", "skill_uid"),
    )


def parse_watches_text(text: str) -> list[Watch]:
    watches: list[Watch] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            label, _, url = line.partition("|")
            label, url = label.strip(), url.strip()
        else:
            label, url = None, line
        watches.append(parse_upwork_search(url, label=label))
    return watches


def candidate_files() -> list[Path]:
    out: list[Path] = []
    if WATCHES_FILE:
        out.append(Path(WATCHES_FILE))
    out.append(Path(AGENT_DIR) / "watches.txt")
    out.append(Path(ROOT_DIR) / "watch_terms.txt")
    return out


def load_watches() -> tuple[list[Watch], str]:
    """Return (watches, source). Source is the file path or "default"."""
    for path in candidate_files():
        if path.is_file():
            watches = parse_watches_text(path.read_text())
            if watches:
                return watches, os.path.normpath(str(path))
    return [Watch(label="n8n", search_expression="n8n")], "default"
