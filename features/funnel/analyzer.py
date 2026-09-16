"""Proposal funnel: stage counts, conversions, win rate, connects economics, segments.

Counts are distinct jobs per stage (the agent logs duplicate NOTIFIED rows).
Win rate is Beta(1, 9)-shrunk toward a 10% prior and flagged `insufficient`
below five submissions, so a 2-for-2 week never prints "100%".
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytz

import config
from core.stats import median
from db import events_since, events_summary, load_classifications, parse_iso

STAGES = ["NOTIFIED", "DRAFTED", "ACCEPTED", "SUBMITTED", "VIEWED", "INTERVIEW", "HIRED"]
SIDE_EXITS = ["FILTERED", "LOST"]
DIMENSIONS = [
    "category",
    "client_segment",
    "budget_band",
    "experience",
    "hour",
    "platform",
    "workflow",
]

# Beta prior: 1 win in 10 → a 10% base rate that the data must earn its way past.
PRIOR_WINS, PRIOR_TRIALS = 1.0, 10.0
MIN_SUBMITTED = 5


@dataclass(slots=True)
class WinRate:
    submitted: int = 0
    hired: int = 0
    raw: float | None = None
    shrunk: float = PRIOR_WINS / PRIOR_TRIALS
    insufficient: bool = True


@dataclass(slots=True)
class SegmentRow:
    dimension: str
    value: str
    notified: int = 0
    drafted: int = 0
    submitted: int = 0
    hired: int = 0
    win: WinRate = field(default_factory=WinRate)


@dataclass(slots=True)
class FunnelReport:
    days: int
    since: str
    jobs: int
    events: int
    stages: dict[str, int]
    side_exits: dict[str, int]
    conversions: list[tuple[str, str, float | None]]
    win: WinRate
    connects_spent: int
    connects_assumed: int  # submissions with no recorded connects → default applied
    cost_per_hire_usd: float | None
    med_winning_bid: float | None
    med_submitted_bid: float | None
    segments: dict[str, list[SegmentRow]]
    sources: dict[str, int]
    last_ingested_at: str | None


def win_rate(submitted: int, hired: int) -> WinRate:
    raw = hired / submitted if submitted else None
    shrunk = (hired + PRIOR_WINS) / (submitted + PRIOR_TRIALS)
    return WinRate(submitted, hired, raw, shrunk, submitted < MIN_SUBMITTED)


# ─── Segment derivations (from event meta) ──────────────────────────────────


def client_segment(meta: dict) -> str:
    # API-sourced proposals carry no client fields at all; that is "unknown", not "risky".
    if not any(k in meta for k in ("verified", "client_spend", "client_hires")):
        return "unknown"
    verified = bool(meta.get("verified"))
    spend = meta.get("client_spend") or 0
    hires = meta.get("client_hires") or 0
    if not verified:
        return "risky"
    if spend >= 10000 and hires >= 5:
        return "champion"
    if hires >= 1:
        return "active"
    return "new"


def budget_band(meta: dict) -> str:
    btype, mid = meta.get("budget_type"), meta.get("budget_mid")
    if btype == "HOURLY" and mid is not None:
        if mid < 25:
            return "hourly <$25"
        if mid < 50:
            return "hourly $25–50"
        if mid < 100:
            return "hourly $50–100"
        return "hourly $100+"
    if btype == "FIXED" and mid is not None:
        if mid < 500:
            return "fixed <$500"
        if mid < 2000:
            return "fixed $500–2k"
        if mid < 5000:
            return "fixed $2k–5k"
        return "fixed $5k+"
    return "unspecified"


def _pretty_category(slug: str) -> str:
    return (slug or "unknown").replace("_", " ").strip() or "unknown"


def _hour_label(ts: str | None, tz_name: str) -> str:
    dt = parse_iso(ts)
    if dt is None:
        return "unknown"
    local = dt.astimezone(pytz.timezone(tz_name))
    return f"{local.hour:02d}:00"


def _first_label(meta: dict, axis: str, cached: dict | None) -> str:
    labels = (meta.get("labels") or {}).get(axis) or []
    if labels:
        return labels[0]
    if cached and cached.get(axis):
        return cached[axis][0]
    return "untagged"


# ─── Analysis ───────────────────────────────────────────────────────────────


def analyze(days: int = 30, tz_name: str | None = None) -> FunnelReport:
    tz_name = tz_name or config.TIMEZONE
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = events_since(since)

    per_job: dict[str, dict] = defaultdict(
        lambda: {"events": set(), "first_ts": {}, "meta": {}, "submitted": [], "hired": False}
    )
    sources: dict[str, int] = defaultdict(int)
    for r in rows:
        sources[r["source"]] += 1
        j = per_job[r["job_id"]]
        ev = r["event"]
        j["events"].add(ev)
        j["first_ts"].setdefault(ev, r["ts"])
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except ValueError:
            meta = {}
        # NOTIFIED carries the segment fields; otherwise the first non-empty meta wins.
        if meta and (ev == "NOTIFIED" or not j["meta"]):
            j["meta"] = meta
        if ev == "SUBMITTED":
            j["submitted"].append(
                {"bid": r["bid_amount"], "connects": r["connects_spent"], "ts": r["ts"]}
            )
        if ev == "HIRED":
            j["hired"] = True

    job_ids = list(per_job)
    cached = load_classifications(job_ids) if job_ids else {}

    stages = {s: sum(1 for j in per_job.values() if s in j["events"]) for s in STAGES}
    side = {s: sum(1 for j in per_job.values() if s in j["events"]) for s in SIDE_EXITS}
    conversions: list[tuple[str, str, float | None]] = []
    for a, b in zip(STAGES, STAGES[1:], strict=False):
        rate = stages[b] / stages[a] if stages[a] else None
        conversions.append((a, b, rate))

    win = win_rate(stages["SUBMITTED"], stages["HIRED"])

    connects_spent = 0
    connects_assumed = 0
    winning_bids: list[float] = []
    submitted_bids: list[float] = []
    for j in per_job.values():
        for sub in j["submitted"]:
            if sub["connects"] is None:
                connects_spent += config.DEFAULT_CONNECTS_PER_PROPOSAL
                connects_assumed += 1
            else:
                connects_spent += int(sub["connects"])
            if sub["bid"] is not None:
                submitted_bids.append(float(sub["bid"]))
                if j["hired"]:
                    winning_bids.append(float(sub["bid"]))
    cost_per_hire = (
        round(connects_spent * config.CONNECT_PRICE_USD / stages["HIRED"], 2)
        if stages["HIRED"]
        else None
    )

    # Segments
    seg: dict[str, dict[str, SegmentRow]] = {d: {} for d in DIMENSIONS}
    for job_id, j in per_job.items():
        meta = j["meta"]
        values = {
            "category": _pretty_category(meta.get("category", "")),
            "client_segment": client_segment(meta) if meta else "unknown",
            "budget_band": budget_band(meta) if meta else "unspecified",
            "experience": (meta.get("experience") or "unspecified").lower(),
            "hour": _hour_label(
                j["first_ts"].get("NOTIFIED") or min(j["first_ts"].values()), tz_name
            ),
            "platform": _first_label(meta, "platform", cached.get(job_id)),
            "workflow": _first_label(meta, "workflow", cached.get(job_id)),
        }
        for dim, value in values.items():
            row = seg[dim].setdefault(value, SegmentRow(dim, value))
            row.notified += "NOTIFIED" in j["events"]
            row.drafted += "DRAFTED" in j["events"]
            row.submitted += "SUBMITTED" in j["events"]
            row.hired += "HIRED" in j["events"]
    segments: dict[str, list[SegmentRow]] = {}
    for dim, rows_by_value in seg.items():
        out = list(rows_by_value.values())
        for row in out:
            row.win = win_rate(row.submitted, row.hired)
        out.sort(key=lambda r: (r.submitted, r.notified), reverse=True)
        segments[dim] = out

    summary = events_summary()
    return FunnelReport(
        days=days,
        since=since,
        jobs=len(per_job),
        events=len(rows),
        stages=stages,
        side_exits=side,
        conversions=conversions,
        win=win,
        connects_spent=connects_spent,
        connects_assumed=connects_assumed,
        cost_per_hire_usd=cost_per_hire,
        med_winning_bid=median(winning_bids) if winning_bids else None,
        med_submitted_bid=median(submitted_bids) if submitted_bids else None,
        segments=segments,
        sources=dict(sources),
        last_ingested_at=summary.get("last_ingested_at"),
    )
