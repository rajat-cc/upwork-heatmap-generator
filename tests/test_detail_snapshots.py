"""Detail-stage snapshots: due windows, the sync step, schema v8 on a v7 table, exact client ids."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from core.ratelimit import TokenBucket

_ORG = {"data": {"organization": {"id": "offline-org"}}}


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _detail(job_id: str, *, hired: int = 0, company: str = "1002306255") -> dict:
    return {"data": {"marketplaceJobPosting": {
        "id": job_id,
        "workFlowState": {"status": "ACTIVE", "closeResult": None},
        "activityStat": {
            "jobActivity": {"invitesSent": 1, "totalHired": hired, "totalInvitedToInterview": 2,
                            "totalOffered": 0, "totalUnansweredInvites": 1,
                            "lastClientActivity": "2026-09-16T11:01:32.019Z"},
            "applicationsBidStats": {"avgRateBid": {"rawValue": "35.5"}, "avgInterviewedRateBid": None,
                                     "minRateBid": {"rawValue": "20"}, "maxRateBid": {"rawValue": "60"}},
        },
        "clientCompanyPublic": {"id": company, "city": "Amman", "country": {"name": "Jordan"}},
        "contractTerms": {"contractType": "FIXED", "personsToHire": 1},
    }}}  # fmt: skip


def test_due_windows_skip_missed_stages_and_legacy_rows(isolated_db):
    import db

    db.init_db()
    db.upsert_jobs(
        [
            {"id": "fresh3h", "published_at": _iso(3)},
            {"id": "day", "published_at": _iso(30)},
            {"id": "late", "published_at": _iso(100)},
            {"id": "old", "published_at": _iso(200)},  # past every window
            {"id": "new1h", "published_at": _iso(1)},  # not yet 2 h old
        ]
    )
    # Later stages first, then the postings closest to leaving their window.
    assert db.due_detail_snapshots() == [("late", "+72h"), ("day", "+24h"), ("fresh3h", "+2h")]

    # A row first seen long ago (legacy import) is never snapshotted.
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET first_seen_at = '2026-01-01T00:00:00Z' WHERE id = 'day'")
    assert ("day", "+24h") not in db.due_detail_snapshots()

    # Recording a stage removes it from the due list and is readable back.
    db.record_detail_snapshot("late", "+72h", {"status": "ACTIVE", "hired": 1, "invites_sent": 3})
    assert db.due_detail_snapshots() == [("fresh3h", "+2h")]
    snap = db.latest_detail_snapshot("late")
    assert snap["stage"] == "+72h" and snap["source"] == "detail"
    assert (
        snap["hired_count"] == 1 and snap["invites_sent"] == 3 and snap["total_applicants"] is None
    )
    assert db.latest_detail_snapshot("fresh3h") is None


def test_take_detail_snapshots_writes_rows_and_client_ids(isolated_db, tmp_path, monkeypatch):
    import db
    import fetcher
    from features.sync.snapshots import take_detail_snapshots

    db.init_db()
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    db.upsert_jobs(
        [
            {"id": "j1", "published_at": _iso(3)},
            {"id": "j2", "published_at": _iso(30)},
            {"id": "gone", "published_at": _iso(4)},
        ]
    )
    probe = tmp_path / "probe.json"

    def transport(body, headers):
        if "OrgId" in body["query"]:
            return fetcher.Response(200, _ORG)
        job_id = body["variables"]["id"]
        if job_id == "gone":
            return fetcher.Response(200, {"data": {"marketplaceJobPosting": None}})
        return fetcher.Response(200, _detail(job_id, hired=1 if job_id == "j2" else 0))

    # Gated until a live probe says the detail query carries activity fields.
    run = take_detail_snapshots(transport=transport, token="t", probe_path=probe)
    assert run.taken == 0 and "make probe" in run.summary()
    probe.write_text(
        json.dumps(
            {"offline": False, "capabilities": {"detail_query": True, "detail_has_activity": False}}
        )
    )
    assert (
        "no activity fields"
        in take_detail_snapshots(transport=transport, token="t", probe_path=probe).summary()
    )
    probe.write_text(
        json.dumps(
            {"offline": False, "capabilities": {"detail_query": True, "detail_has_activity": True}}
        )
    )

    # The cap binds later stages first.
    capped = take_detail_snapshots(transport=transport, token="t", probe_path=probe, limit=1)
    assert (capped.due, capped.taken) == (3, 1) and db.latest_detail_snapshot("j2")[
        "stage"
    ] == "+24h"

    run = take_detail_snapshots(transport=transport, token="t", probe_path=probe)
    assert (run.due, run.taken, run.missing, run.errors, run.clients_identified) == (2, 1, 1, 0, 1)
    assert run.summary() == "1 taken of 2 due, 1 gone, 1 client ids"
    j2 = db.latest_detail_snapshot("j2")
    assert j2["hired_count"] == 1 and j2["interviews"] == 2 and j2["offers"] == 0
    assert j2["avg_bid"] == 35.5 and j2["avg_interviewed_bid"] is None and j2["max_bid"] == 60.0
    assert j2["job_status"] == "ACTIVE" and j2["last_client_activity"].startswith("2026-09-16")
    assert db.latest_detail_snapshot("gone")["job_status"] == "MISSING"
    with db.get_conn() as conn:
        ids = dict(conn.execute("SELECT id, client_company_id FROM jobs").fetchall())
    assert ids == {"j1": "1002306255", "j2": "1002306255", "gone": ""}

    # Nothing is due any more, and a later search re-fetch keeps the company id.
    assert take_detail_snapshots(transport=transport, token="t", probe_path=probe).due == 0
    db.upsert_jobs([{"id": "j1", "published_at": _iso(3), "total_applicants": 4}])
    with db.get_conn() as conn:
        assert conn.execute("SELECT client_company_id FROM jobs WHERE id = 'j1'").fetchone()[0] == (
            "1002306255"
        )

    # A permission error is an answer, not a glitch: the run stops.
    def denied(body, headers):
        if "OrgId" in body["query"]:
            return fetcher.Response(200, _ORG)
        return fetcher.Response(
            200,
            {"data": None, "errors": [{"message": "Your OAuth2 permissions do not allow access to this resource (marketplaceJobPosting)."}]},
        )  # fmt: skip

    db.upsert_jobs([{"id": "j3", "published_at": _iso(5)}])
    run = take_detail_snapshots(transport=denied, token="t", probe_path=probe)
    assert run.taken == 0 and run.summary().startswith("stopped:")
    assert ("j3", "+2h") in db.due_detail_snapshots()  # still due next time


def test_schema_v8_adds_detail_columns_to_a_v7_snapshot_table(isolated_db):
    with sqlite3.connect(isolated_db) as conn:
        conn.executescript(
            """
            CREATE TABLE job_snapshots (
                job_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'search', total_applicants INTEGER,
                hired_count INTEGER, invites_sent INTEGER, proposals_tier TEXT,
                source TEXT NOT NULL DEFAULT 'search',
                PRIMARY KEY (job_id, observed_at)
            );
            INSERT INTO job_snapshots (job_id, observed_at, total_applicants)
            VALUES ('legacy', '2026-05-01T00:00:00Z', 7);
            """
        )
    import db

    db.init_db()
    db.init_db()  # idempotent
    with sqlite3.connect(isolated_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(job_snapshots)")}
        jobs_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_meta ORDER BY version")]
        dupes = conn.execute(
            "SELECT version FROM schema_meta GROUP BY version HAVING COUNT(*) > 1"
        ).fetchall()
    assert set(db._SNAPSHOT_DETAIL_COLUMNS) <= cols
    assert "client_company_id" in jobs_cols
    assert versions[-1] == db.SCHEMA_VERSION == 8 and not dupes
    assert db.snapshots_for("legacy")[0]["total_applicants"] == 7


def test_clients_group_on_the_exact_company_id_when_known(isolated_db):
    import db
    from features.clients.analyzer import analyze, fingerprint

    db.init_db()
    db.upsert_jobs(
        [
            {"id": "e1", "published_at": _iso(3), "client_country": "JO",
             "client_total_spent": 100, "client_total_hires": 1, "client_total_posted": 2},
            {"id": "e2", "published_at": _iso(50), "client_country": "JO",
             "client_total_spent": 900, "client_total_hires": 4, "client_total_posted": 9},
            {"id": "h1", "published_at": _iso(3), "client_country": "US",
             "client_total_spent": 500, "client_total_hires": 3, "client_total_posted": 5},
            {"id": "h2", "published_at": _iso(3), "client_country": "US",
             "client_total_spent": 500, "client_total_hires": 3, "client_total_posted": 5},
        ]
    )  # fmt: skip
    db.record_detail_snapshot("e1", "+2h", {"status": "ACTIVE", "client_company_id": "777"})
    db.record_detail_snapshot("e2", "+24h", {"status": "ACTIVE", "client_company_id": "777"})

    from core.models import Job

    assert fingerprint(Job.from_row(db.get_jobs_by_ids(["e1"])[0])) == "id:777"
    report = analyze(days=7)
    rows = {r.key: r for r in report.rows}
    exact = rows["id:777"]
    assert exact.exact and exact.posts == 2 and exact.label == "#777" and exact.match == "exact"
    assert sorted(exact.job_ids) == ["e1", "e2"]
    heuristic = [r for r in report.rows if not r.exact]
    assert len(heuristic) == 1 and heuristic[0].posts == 2 and heuristic[0].match == "heuristic"
