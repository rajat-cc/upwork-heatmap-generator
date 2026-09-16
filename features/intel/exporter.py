"""Write `exports/market_intel_latest.json`: the aggregates the proposal agent
can fold into a prompt. Aggregates only: bid bands, demand by axis, watch
suggestions and a funnel summary. No job ids, no titles, no free text, so the
file is both terms-of-service safe and prompt-injection safe.

Written atomically (tmp + rename). The consumer on the agent side is a later
change outside this repo; the contract is `features/intel/schema.py`.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import config
from core.logging_setup import get_logger
from core.scoring import get_scoring
from core.stats import median
from db import get_conn, get_last_success
from features.bands.analyzer import ALL
from features.bands.analyzer import analyze as analyze_bands
from features.funnel.analyzer import analyze as analyze_funnel
from features.intel.schema import SCHEMA_VERSION, validate

log = get_logger(__name__)
FILENAME = "market_intel_latest.json"
TOP_DEMAND_PER_AXIS = 12
TOP_WATCHES = 8


def _demand(since: str) -> list[dict]:
    """Jobs per cached label, per axis, among jobs published since `since`."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE published_at >= ?", (since,)
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT c.axis, c.label, COUNT(DISTINCT c.job_id) AS n
              FROM job_classifications c JOIN jobs j ON j.id = c.job_id
             WHERE j.published_at >= ?
             GROUP BY c.axis, c.label
            """,
            (since,),
        ).fetchall()
    per_axis: dict[str, list] = defaultdict(list)
    for r in rows:
        per_axis[r["axis"]].append(r)
    out: list[dict] = []
    for axis, items in sorted(per_axis.items()):
        items.sort(key=lambda r: -r["n"])
        for r in items[:TOP_DEMAND_PER_AXIS]:
            out.append(
                {
                    "axis": axis,
                    "label": r["label"],
                    "jobs": int(r["n"]),
                    "share": round(r["n"] / total, 4) if total else 0.0,
                }
            )
    return out


def _watch_suggestions() -> list[dict]:
    """Platform and workflow labels with the most posts in the last 7 days."""
    since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.axis, c.label, j.total_applicants
              FROM job_classifications c JOIN jobs j ON j.id = c.job_id
             WHERE j.published_at >= ? AND c.axis IN ('platform', 'workflow')
            """,
            (since,),
        ).fetchall()
    counts: Counter[tuple[str, str]] = Counter()
    applicants: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in rows:
        key = (r["axis"], r["label"])
        counts[key] += 1
        applicants[key].append(int(r["total_applicants"] or 0))
    out = []
    for (axis, label), n in counts.most_common(TOP_WATCHES):
        out.append(
            {
                "expression": label.lower(),
                "axis": axis,
                "jobs_7d": int(n),
                "median_applicants": median(applicants[(axis, label)]),
            }
        )
    return out


def build(days: int = 30) -> dict:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    bands = analyze_bands(days)
    funnel = analyze_funnel(days)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_as_of": get_last_success() or "never",
        "window_days": days,
        "scoring_version": get_scoring().version,
        "bands": [
            {
                "workflow": ALL if r.workflow == ALL else r.workflow,
                "client_segment": r.client_segment,
                "experience": r.experience,
                "budget_type": r.budget_type,
                "level": r.level,
                "n": r.n,
                "p25": r.p25,
                "p50": r.p50,
                "p75": r.p75,
            }
            for r in bands.rows
        ],
        "demand": _demand(since),
        "watch_suggestions": _watch_suggestions(),
        "funnel_summary": {
            "days": days,
            "stages": funnel.stages,
            "win_rate_shrunk": round(funnel.win.shrunk, 4),
            "win_rate_raw": round(funnel.win.raw, 4) if funnel.win.raw is not None else None,
            "insufficient": funnel.win.insufficient,
            "cost_per_hire_usd": funnel.cost_per_hire_usd,
            "median_winning_bid": funnel.med_winning_bid,
        },
    }
    problems = validate(doc)
    if problems:
        raise ValueError("market intel document invalid: " + "; ".join(problems))
    return doc


def write(doc: dict, path: str | None = None) -> str:
    target = path or os.path.join(config.EXPORTS_DIR, FILENAME)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    os.replace(tmp, target)
    return target


def run(days: int = 30, path: str | None = None) -> str:
    return write(build(days), path)
