"""Trend % must not appear unless the window holds real, repeated observation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _seed_python_jobs(prompt: bool):
    """Six jobs in each half of a 14-day window, all tagged python."""
    from db import get_conn, init_db, upsert_jobs

    init_db()
    now = datetime.now(UTC)
    rows = []
    for i in range(6):
        rows.append(
            {
                "id": f"old-{i}",
                "published_at": _iso(now - timedelta(days=10, hours=i)),
                "skills": json.dumps(["python"]),
                "budget_type": "HOURLY",
                "budget_amount": 40,
            }
        )
        rows.append(
            {
                "id": f"new-{i}",
                "published_at": _iso(now - timedelta(days=3, hours=i)),
                "skills": json.dumps(["python"]),
                "budget_type": "HOURLY",
                "budget_amount": 50,
            }
        )
    upsert_jobs(rows)  # fmt: skip
    if prompt:
        # Pretend every job was discovered one hour after it was published.
        with get_conn() as conn:
            for r in rows:
                seen = _iso(datetime.fromisoformat(r["published_at"]) + timedelta(hours=1)) + "Z"
                conn.execute("UPDATE jobs SET first_seen_at = ? WHERE id = ?", (seen, r["id"]))


def _add_runs(n: int):
    from db import finish_fetch_run, start_fetch_run

    for _ in range(n):
        finish_fetch_run(start_fetch_run("python"), jobs_seen=12, jobs_new=0)


def test_trend_hidden_when_fewer_than_three_runs(isolated_db):
    from features.dashboard.analyzer import skills_stats

    _seed_python_jobs(prompt=True)
    _add_runs(2)
    row = next(r for r in skills_stats(days=14) if r["skill"] == "Python")
    assert row["trend_pct"] is None
    assert row["trend_reason"] == "runs"


def test_trend_shown_with_three_runs_and_prompt_discovery(isolated_db):
    from features.dashboard.analyzer import skills_stats

    _seed_python_jobs(prompt=True)
    _add_runs(3)
    row = next(r for r in skills_stats(days=14) if r["skill"] == "Python")
    assert row["trend_reason"] == "ok"
    assert row["trend_pct"] == 0  # six in each half
    assert row["med_hourly"] == 45.0  # median of the two budgets, not a mean of outliers


def test_trend_hidden_when_jobs_were_discovered_late(isolated_db):
    from features.dashboard.analyzer import skills_stats

    _seed_python_jobs(prompt=False)  # first_seen_at = now, days after publish
    _add_runs(3)
    row = next(r for r in skills_stats(days=14) if r["skill"] == "Python")
    assert row["trend_pct"] is None
    assert row["trend_reason"] == "sample"
