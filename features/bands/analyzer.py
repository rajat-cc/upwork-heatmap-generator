"""Bid bands: p25 / median / p75 of what jobs pay, and where your bids sit.

Cells are (workflow, client segment, experience, budget type). A cell with
fewer than `min_band_sample` jobs rolls up to its workflow band, and a
workflow with too few to its budget-type band, so every row printed rests on
a real sample and says which level it came from.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.models import Job
from core.scoring import client_segment_of, get_scoring
from core.stats import band
from db import events_since, get_conn, load_classifications

ALL = "all"


@dataclass(slots=True)
class BandRow:
    workflow: str
    client_segment: str
    experience: str
    budget_type: str
    n: int
    p25: float
    p50: float
    p75: float
    level: str  # cell | workflow | budget_type


@dataclass(slots=True)
class BidRow:
    job_id: str
    ts: str
    bid_amount: float
    bid_type: str
    workflow: str
    client_segment: str
    outcome: str  # hired | lost | pending
    band_p25: float | None
    band_p50: float | None
    band_p75: float | None
    band_level: str
    position: str  # below | in | above | no band


@dataclass(slots=True)
class BandsReport:
    days: int
    since: str
    jobs: int
    min_n: int
    rows: list[BandRow]
    bids: list[BidRow]
    by_key: dict[tuple, BandRow] = field(default_factory=dict)


def _value(job: Job) -> float | None:
    if job.budget_type == "HOURLY":
        v = (
            (job.budget_min + job.budget_max) / 2
            if (job.budget_min and job.budget_max)
            else job.budget_amount
        )
        return v or None
    if job.budget_type == "FIXED" and job.budget_amount > 0:
        return job.budget_amount
    return None


def _experience(job: Job) -> str:
    return {"ENTRY_LEVEL": "entry", "INTERMEDIATE": "intermediate", "EXPERT": "expert"}.get(
        job.contractor_tier or "", "unknown"
    )


def analyze(days: int = 30, min_n: int | None = None) -> BandsReport:
    scoring = get_scoring()
    min_n = int(min_n or scoring.thresholds.get("min_band_sample", 8))
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE published_at >= ? AND budget_type IN ('HOURLY','FIXED') "
            "AND budget_amount > 0",
            (since,),
        ).fetchall()
    jobs = [Job.from_row(r) for r in rows]
    labels = load_classifications([j.id for j in jobs]) if jobs else {}

    cells: dict[tuple, list[float]] = defaultdict(list)
    for job in jobs:
        value = _value(job)
        if value is None:
            continue
        workflows = (labels.get(job.id, {}).get("workflow") or ["untagged"])[:1]
        seg = client_segment_of(job, scoring)
        exp = _experience(job)
        for wf in workflows:
            cells[(wf, seg, exp, job.budget_type)].append(value)
            cells[(wf, ALL, ALL, job.budget_type)].append(value)
            cells[(ALL, ALL, ALL, job.budget_type)].append(value)

    out: list[BandRow] = []
    by_key: dict[tuple, BandRow] = {}
    for key, values in cells.items():
        if len(values) < min_n:
            continue
        p25, p50, p75 = band(values)
        wf, seg, exp, bt = key
        level = "budget_type" if wf == ALL else "workflow" if seg == ALL else "cell"
        row = BandRow(
            wf, seg, exp, bt, len(values), round(p25, 2), round(p50, 2), round(p75, 2), level
        )
        out.append(row)
        by_key[key] = row
    out.sort(key=lambda r: ({"budget_type": 0, "workflow": 1, "cell": 2}[r.level], -r.n))

    bids = _bids(since, by_key, labels)
    return BandsReport(
        days=days, since=since, jobs=len(jobs), min_n=min_n, rows=out, bids=bids, by_key=by_key
    )


def lookup(
    by_key: dict[tuple, BandRow], workflow: str, segment: str, experience: str, budget_type: str
) -> BandRow | None:
    """Most specific band available: cell → workflow → budget type."""
    for key in (
        (workflow, segment, experience, budget_type),
        (workflow, ALL, ALL, budget_type),
        (ALL, ALL, ALL, budget_type),
    ):
        if key in by_key:
            return by_key[key]
    return None


def _bids(since: str, by_key: dict[tuple, BandRow], labels: dict) -> list[BidRow]:
    events = events_since(since)
    outcome: dict[str, str] = {}
    submitted: list = []
    metas: dict[str, dict] = {}
    for e in events:
        if e["event"] == "HIRED":
            outcome[e["job_id"]] = "hired"
        elif e["event"] == "LOST":
            outcome.setdefault(e["job_id"], "lost")
        try:
            meta = json.loads(e["meta_json"] or "{}")
        except ValueError:
            meta = {}
        if meta and (e["event"] == "NOTIFIED" or e["job_id"] not in metas):
            metas[e["job_id"]] = meta
        if e["event"] == "SUBMITTED" and e["bid_amount"]:
            submitted.append(e)

    from features.funnel.analyzer import client_segment as seg_from_meta  # lazy: sibling feature

    out: list[BidRow] = []
    for e in submitted:
        job_id = e["job_id"]
        meta = metas.get(job_id, {})
        wf = (
            (meta.get("labels") or {}).get("workflow")
            or labels.get(job_id, {}).get("workflow")
            or ["untagged"]
        )[0]
        seg = seg_from_meta(meta) if meta else "unknown"
        bid_type = "HOURLY" if (e["bid_type"] or "").lower().startswith("hour") else "FIXED"
        exp = (meta.get("experience") or "unknown").lower().replace(" level", "")
        row = lookup(by_key, wf, seg, exp, bid_type)
        amount = float(e["bid_amount"])
        if row is None:
            position = "no band"
        elif amount < row.p25:
            position = "below"
        elif amount > row.p75:
            position = "above"
        else:
            position = "in"
        out.append(
            BidRow(
                job_id=job_id,
                ts=e["ts"],
                bid_amount=amount,
                bid_type=bid_type,
                workflow=wf,
                client_segment=seg,
                outcome=outcome.get(job_id, "pending"),
                band_p25=row.p25 if row else None,
                band_p50=row.p50 if row else None,
                band_p75=row.p75 if row else None,
                band_level=row.level if row else "none",
                position=position,
            )
        )
    out.sort(key=lambda b: b.ts, reverse=True)
    return out
