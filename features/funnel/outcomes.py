"""Manual outcome capture (heatmap-only: no proposal-agent changes needed).

`main.py outcome <job> submitted --bid 45 --bid-type hourly --connects 16`
writes one event to `proposal_events` and appends it to
`data/outcomes/outcomes-YYYYMM.jsonl`, a portable ledger the ingest replays
into a rebuilt database. `--from-csv` bulk-loads a catch-up file with the
columns `job,event,ts,bid_amount,bid_type,connects,note`.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import config
from db import EVENT_TYPES, find_job_id_by_url, insert_events
from features.funnel.ingest import event_id

MANUAL_EVENTS = ("SUBMITTED", "VIEWED", "INTERVIEW", "HIRED", "LOST")
_NUMERIC = re.compile(r"^\d{6,}$")


def resolve_job_id(ref: str) -> str:
    """Accept a numeric id, a job URL or a `~…` ciphertext; return the ledger's id."""
    ref = (ref or "").strip()
    if _NUMERIC.match(ref):
        return ref
    mapped = find_job_id_by_url(ref)
    return mapped or ref.rstrip("/").rsplit("/", 1)[-1]


def build_outcome(
    job_ref: str,
    event: str,
    *,
    bid_amount: float | None = None,
    bid_type: str | None = None,
    connects: int | None = None,
    note: str = "",
    ts: str | None = None,
    source: str = "manual",
) -> dict:
    ev = event.strip().upper()
    if ev not in EVENT_TYPES:
        raise ValueError(f"unknown event {event!r}; expected one of {', '.join(MANUAL_EVENTS)}")
    stamp = (ts or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")).strip()
    job_id = resolve_job_id(job_ref)
    return {
        "event_id": event_id(source, job_id, ev, stamp),
        "job_id": job_id,
        "event": ev,
        "ts": stamp,
        "source": source,
        "bid_amount": float(bid_amount) if bid_amount is not None else None,
        "bid_type": (bid_type or "").lower() or None,
        "connects_spent": int(connects) if connects is not None else None,
        "meta": {"note": note or "", "job_ref": job_ref},
    }


def _ledger_path(ts: str, outcomes_dir: str | Path | None = None) -> Path:
    directory = Path(outcomes_dir or config.OUTCOMES_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"outcomes-{ts[:7].replace('-', '')}.jsonl"


def record_outcomes(events: list[dict], *, outcomes_dir: str | Path | None = None) -> int:
    """Insert into the DB and append to the monthly JSONL ledger. Returns new rows."""
    inserted = insert_events(events)
    for e in events:
        with open(_ledger_path(e["ts"], outcomes_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    return inserted


def record_outcome(job_ref: str, event: str, **kwargs) -> tuple[dict, int]:
    outcomes_dir = kwargs.pop("outcomes_dir", None)
    e = build_outcome(job_ref, event, **kwargs)
    return e, record_outcomes([e], outcomes_dir=outcomes_dir)


def outcomes_from_csv(path: str | Path) -> list[dict]:
    """Bulk catch-up: columns job,event[,ts,bid_amount,bid_type,connects,note]."""
    out: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not (row.get("job") and row.get("event")):
                continue
            out.append(
                build_outcome(
                    row["job"],
                    row["event"],
                    bid_amount=float(row["bid_amount"]) if row.get("bid_amount") else None,
                    bid_type=row.get("bid_type") or None,
                    connects=int(row["connects"]) if row.get("connects") else None,
                    note=row.get("note") or "",
                    ts=row.get("ts") or None,
                )
            )
    return out


def ledger_files(outcomes_dir: str | Path | None = None) -> list[str]:
    directory = Path(outcomes_dir or config.OUTCOMES_DIR)
    if not directory.is_dir():
        return []
    return sorted(os.path.join(str(directory), p.name) for p in directory.glob("outcomes-*.jsonl"))
