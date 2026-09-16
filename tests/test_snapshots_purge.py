"""Search-stage snapshots on re-observation, and the retention purge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_snapshot_written_on_first_sight_and_on_change(isolated_db, monkeypatch):
    import db

    db.init_db()
    monkeypatch.setattr(db, "SNAPSHOT_MIN_GAP_HOURS", 2.0)

    db.upsert_jobs([{"id": "j1", "total_applicants": 5}])
    assert [r["total_applicants"] for r in db.snapshots_for("j1")] == [5]

    db.upsert_jobs([{"id": "j1", "total_applicants": 5}])  # same count, within the gap
    assert len(db.snapshots_for("j1")) == 1

    db.upsert_jobs([{"id": "j1", "total_applicants": 9}])  # changed → new observation
    snaps = db.snapshots_for("j1")
    assert [r["total_applicants"] for r in snaps] == [5, 9]
    assert {r["stage"] for r in snaps} == {"search"}


def test_snapshot_written_when_gap_elapsed(isolated_db, monkeypatch):
    import db

    db.init_db()
    monkeypatch.setattr(db, "SNAPSHOT_MIN_GAP_HOURS", 0.0)
    db.upsert_jobs([{"id": "j1", "total_applicants": 5}])
    # Force a distinct observed_at (PK) by backdating the first snapshot.
    with db.get_conn() as conn:
        conn.execute("UPDATE job_snapshots SET observed_at = '2026-01-01T00:00:00Z'")
    db.upsert_jobs([{"id": "j1", "total_applicants": 5}])
    assert len(db.snapshots_for("j1")) == 2


def _backdate(db, job_id: str, hours: float):
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET last_fetched_at = ? WHERE id = ?", (stamp, job_id))


def test_purge_blanks_text_but_keeps_derived_rows(isolated_db):
    import db
    from features.n8n import classifier

    db.init_db()
    db.upsert_jobs(
        [
            {"id": "old", "title": "n8n CRM sync for a clinic", "description": "hubspot to airtable",
             "skills": '["n8n"]', "client_total_hires": 3, "client_total_posted": 4},
            {"id": "fresh", "title": "fresh job", "description": "keep me"},
        ]
    )  # fmt: skip
    classifier.persist(classifier.classify_rows(db.get_jobs_by_ids(["old"])))
    _backdate(db, "old", hours=30)

    assert db.purge_text(24, dry_run=True) == 1
    assert db.purge_text(24) == 1
    assert db.purge_text(24) == 0  # already purged

    with db.get_conn() as conn:
        old = conn.execute("SELECT * FROM jobs WHERE id = 'old'").fetchone()
        fresh = conn.execute("SELECT * FROM jobs WHERE id = 'fresh'").fetchone()
        labels = conn.execute(
            "SELECT axis, label FROM job_classifications WHERE job_id = 'old' ORDER BY axis, label"
        ).fetchall()
        fts = conn.execute(
            "SELECT count(*) FROM jobs_fts WHERE jobs_fts MATCH 'clinic'"
        ).fetchone()[0]
    assert old["title"] == "" and old["description"] == "" and old["purged_at"]
    assert old["skills"] == '["n8n"]' and old["hire_rate"] == 0.75  # derived data stays
    assert fresh["description"] == "keep me" and fresh["purged_at"] is None
    assert ("platform", "n8n") in [tuple(r) for r in labels]
    assert ("industry", "Healthcare") in [tuple(r) for r in labels]
    assert fts == 0  # purged text left the index too


def test_fresh_fetch_restarts_the_retention_clock(isolated_db):
    import db

    db.init_db()
    db.upsert_jobs([{"id": "j", "title": "t", "description": "d"}])
    _backdate(db, "j", hours=30)
    assert db.purge_text(24) == 1
    db.upsert_jobs([{"id": "j", "title": "t2", "description": "d2"}])
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT title, description, purged_at FROM jobs WHERE id = 'j'"
        ).fetchone()
    assert tuple(row) == ("t2", "d2", None)


def test_purge_only_touches_whitelisted_fields(isolated_db):
    import db

    db.init_db()
    db.upsert_jobs([{"id": "j", "title": "t", "description": "d"}])
    _backdate(db, "j", hours=30)
    assert db.purge_text(24, fields=("description", "skills")) == 1  # skills is ignored
    with db.get_conn() as conn:
        row = conn.execute("SELECT title, description, skills FROM jobs WHERE id = 'j'").fetchone()
    assert tuple(row) == ("t", "", "[]")
