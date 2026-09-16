"""Ingest proposal-funnel events from the proposal agent's files (read-only).

Sources:
  * `alerts.csv` — the agent's append-only alert log. Its header has 13
    columns; rows written after a later release have 14 (`matched` was inserted
    after `url`). Rows are therefore mapped by their own length, never by the
    header. Events: NOTIFIED, FILTERED.
  * `agent.db` — DRAFTED (from `drafted_ts`, falling back to the first
    proposal version) and ACCEPTED (`decided_ts` on accepted jobs). Opened
    read-only with a timeout; the agent owns that file.
  * `data/outcomes/*.jsonl` — this repo's own manual outcome ledger
    (see `outcomes.py`), replayed so a rebuilt database regains them.

Every event gets a deterministic id, so re-ingesting is idempotent.
Segment fields (budget, client history, category, experience, title-only
labels) travel in `meta`, so the funnel can slice jobs the heatmap never
fetched. No job rows are created for agent-only jobs: they have no
`published_at`, and every analyzer filters on it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import config
from core.logging_setup import get_logger
from db import insert_events
from features.n8n.classifier import INDUSTRIES_RE, PLATFORMS_RE, WORKFLOWS_RE
from taxonomies.compile import match_categories

log = get_logger(__name__)

HEADER_13 = [
    "ts", "event", "job_id", "title", "url", "budget", "category",
    "client_spend", "client_hires", "verified", "proposals", "experience", "reason",
]  # fmt: skip
HEADER_14 = [
    "ts", "event", "job_id", "title", "url", "matched", "budget", "category",
    "client_spend", "client_hires", "verified", "proposals", "experience", "reason",
]  # fmt: skip

_HOURLY_RANGE = re.compile(r"\$([\d,\.]+)\s*[–—-]\s*\$?([\d,\.]+)\s*/hr", re.I)
_HOURLY = re.compile(r"\$([\d,\.]+)\s*/hr", re.I)
_FIXED = re.compile(r"\$([\d,\.]+)\s*fixed", re.I)
_MONEY = re.compile(r"[\d,\.]+")


@dataclass
class IngestResult:
    sources: dict[str, dict] = field(default_factory=dict)
    inserted: int = 0
    seen: int = 0

    def add(self, name: str, seen: int, inserted: int, **extra) -> None:
        self.sources[name] = {"seen": seen, "inserted": inserted, **extra}
        self.seen += seen
        self.inserted += inserted


# ─── Parsing helpers ────────────────────────────────────────────────────────


def event_id(source: str, job_id: str, event: str, ts: str) -> str:
    """Deterministic id: same source + job + event within the same minute → same id."""
    key = f"{source}|{job_id}|{event}|{(ts or '')[:16]}"
    return hashlib.sha1(key.encode()).hexdigest()[:24]


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = _MONEY.search(text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_budget(label: str | None) -> dict:
    """`$10–$25/hr` → hourly 10–25 (mid 17.5); `$600 fixed` → fixed 600; else unknown."""
    text = (label or "").strip()
    m = _HOURLY_RANGE.search(text)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        mid = (lo + hi) / 2 if lo is not None and hi is not None else lo or hi
        return {"budget_type": "HOURLY", "budget_min": lo, "budget_max": hi, "budget_mid": mid}
    m = _HOURLY.search(text)
    if m:
        v = _num(m.group(1))
        return {"budget_type": "HOURLY", "budget_min": v, "budget_max": v, "budget_mid": v}
    m = _FIXED.search(text)
    if m:
        v = _num(m.group(1))
        return {"budget_type": "FIXED", "budget_min": v, "budget_max": v, "budget_mid": v}
    return {"budget_type": "UNKNOWN", "budget_min": None, "budget_max": None, "budget_mid": None}


def title_labels(title: str | None) -> dict[str, list[str]]:
    """Regex labels from the title alone, so segments exist without job text."""
    text = title or ""
    return {
        "industry": match_categories(text, INDUSTRIES_RE),
        "workflow": match_categories(text, WORKFLOWS_RE),
        "platform": match_categories(text, PLATFORMS_RE),
    }


def _epoch_iso(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


# ─── alerts.csv ─────────────────────────────────────────────────────────────


def read_alerts_csv(path: str | Path) -> tuple[list[dict], int]:
    """Rows mapped by their own column count. Returns (rows, skipped)."""
    rows: list[dict] = []
    skipped = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return rows, 0
        for raw in reader:
            if len(raw) == 14:
                rows.append(dict(zip(HEADER_14, raw, strict=True)))
            elif len(raw) == 13:
                rows.append(dict(zip(HEADER_13, raw, strict=True)))
            else:
                skipped += 1
    return rows, skipped


def events_from_alerts(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        ev = (r.get("event") or "").upper()
        job_id = (r.get("job_id") or "").strip()
        ts = (r.get("ts") or "").strip()
        if ev not in ("NOTIFIED", "FILTERED") or not job_id or not ts:
            continue
        hires = _num(r.get("client_hires"))
        proposals = _num(r.get("proposals"))
        meta = {
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "matched": r.get("matched") or "",
            "budget": r.get("budget") or "",
            **parse_budget(r.get("budget")),
            "category": r.get("category") or "",
            "client_spend": _num(r.get("client_spend")),
            "client_hires": int(hires) if hires is not None else None,
            "verified": (r.get("verified") or "").strip().lower() in ("yes", "true", "1"),
            "proposals": int(proposals) if proposals is not None else None,
            "experience": r.get("experience") or "",
            "reason": r.get("reason") or "",
            "labels": title_labels(r.get("title")),
        }
        out.append(
            {
                "event_id": event_id("agent_csv", job_id, ev, ts),
                "job_id": job_id,
                "event": ev,
                "ts": ts,
                "source": "agent_csv",
                "meta": meta,
            }
        )
    return out


# ─── agent.db ───────────────────────────────────────────────────────────────


def events_from_agent_db(path: str | Path) -> list[dict]:
    uri = f"file:{Path(path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    out: list[dict] = []
    try:
        first_version = {
            r["job_id"]: r["ts"]
            for r in conn.execute(
                "SELECT job_id, MIN(created_ts) AS ts FROM proposal_versions GROUP BY job_id"
            )
        }
        rows = conn.execute(
            "SELECT job_id, title, category, budget_label, status, created_date_time, "
            "drafted_ts, decided_ts FROM jobs WHERE status IN ('DRAFTED', 'ACCEPTED')"
        ).fetchall()
    finally:
        conn.close()

    for r in rows:
        job_id = r["job_id"]
        meta = {
            "title": r["title"] or "",
            "category": r["category"] or "",
            "budget": r["budget_label"] or "",
            **parse_budget(r["budget_label"]),
            "created_date_time": r["created_date_time"] or "",
            "labels": title_labels(r["title"]),
        }
        drafted = _epoch_iso(r["drafted_ts"]) or _epoch_iso(first_version.get(job_id))
        if drafted:
            out.append(
                {
                    "event_id": event_id("agent_db", job_id, "DRAFTED", drafted),
                    "job_id": job_id,
                    "event": "DRAFTED",
                    "ts": drafted,
                    "source": "agent_db",
                    "meta": meta,
                }
            )
        if r["status"] == "ACCEPTED":
            accepted = _epoch_iso(r["decided_ts"]) or drafted
            if accepted:
                out.append(
                    {
                        "event_id": event_id("agent_db", job_id, "ACCEPTED", accepted),
                        "job_id": job_id,
                        "event": "ACCEPTED",
                        "ts": accepted,
                        "source": "agent_db",
                        "meta": meta,
                    }
                )
    return out


# ─── outcomes JSONL (this repo's own ledger) ────────────────────────────────


def events_from_jsonl_dir(path: str | Path) -> list[dict]:
    out: list[dict] = []
    directory = Path(path)
    if not directory.is_dir():
        return out
    for file in sorted(directory.glob("outcomes-*.jsonl")):
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    log.warning("Skipping malformed line in %s", file.name)
                    continue
                if not all(k in e for k in ("event_id", "job_id", "event", "ts")):
                    continue
                e.setdefault("source", "jsonl")
                out.append(e)
    return out


# ─── Orchestration ──────────────────────────────────────────────────────────


def ingest(
    agent_dir: str | None = None,
    *,
    alerts_csv: str | Path | None = None,
    agent_db: str | Path | None = None,
    outcomes_dir: str | Path | None = None,
) -> IngestResult:
    """Ingest every available source; missing files are reported, not errors."""
    base = Path(agent_dir or config.AGENT_DIR)
    alerts_csv = Path(alerts_csv) if alerts_csv else base / "data" / "alerts.csv"
    agent_db = Path(agent_db) if agent_db else base / "data" / "agent.db"
    outcomes_dir = Path(outcomes_dir) if outcomes_dir else Path(config.OUTCOMES_DIR)
    result = IngestResult()

    if alerts_csv.is_file():
        rows, skipped = read_alerts_csv(alerts_csv)
        events = events_from_alerts(rows)
        result.add(
            "alerts_csv",
            len(events),
            insert_events(events),
            skipped_rows=skipped,
            path=str(alerts_csv),
        )
    else:
        result.add("alerts_csv", 0, 0, missing=True, path=str(alerts_csv))

    if agent_db.is_file():
        try:
            events = events_from_agent_db(agent_db)
            result.add("agent_db", len(events), insert_events(events), path=str(agent_db))
        except sqlite3.Error as exc:
            log.error("agent.db unreadable: %s", exc)
            result.add("agent_db", 0, 0, error=str(exc), path=str(agent_db))
    else:
        result.add("agent_db", 0, 0, missing=True, path=str(agent_db))

    events = events_from_jsonl_dir(outcomes_dir)
    result.add("outcomes_jsonl", len(events), insert_events(events), path=str(outcomes_dir))
    os.makedirs(outcomes_dir, exist_ok=True)
    return result
