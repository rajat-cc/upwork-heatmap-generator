"""upsert_jobs accepts partial dicts and preserves cohort columns on refresh."""

from __future__ import annotations


def test_upsert_minimal_dict_uses_defaults(isolated_db):
    from db import get_conn, init_db, upsert_jobs

    init_db()
    seen, new = upsert_jobs([{"id": "j1", "title": "T"}])
    assert (seen, new) == (1, 1)

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = 'j1'").fetchone()
    assert row["description"] == ""
    assert row["skills"] == "[]"
    assert row["contractor_tier"] == "UNKNOWN"
    assert row["budget_type"] == "UNKNOWN"
    assert row["fetch_count"] == 1
    assert row["url"] == "https://www.upwork.com/jobs/j1"
    assert row["first_seen_at"] and row["first_seen_at"] == row["last_fetched_at"]


def test_upsert_twice_preserves_first_seen_and_increments_fetch_count(isolated_db):
    from db import get_conn, init_db, upsert_jobs

    init_db()
    upsert_jobs([{"id": "j1", "title": "T", "skills": '["python"]'}], search_term="first")
    with get_conn() as conn:
        before = conn.execute("SELECT first_seen_at FROM jobs WHERE id = 'j1'").fetchone()[0]

    seen, new = upsert_jobs([{"id": "j1", "title": "T2", "skills": '["python", "n8n"]'}], "second")
    assert (seen, new) == (1, 0)

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = 'j1'").fetchone()
        skills = {r[0] for r in conn.execute("SELECT skill FROM job_skills WHERE job_id = 'j1'")}
    assert row["title"] == "T2"
    assert row["fetch_count"] == 2
    assert row["first_seen_at"] == before
    assert row["discovered_via_search"] == "first"  # never overwritten
    assert skills == {"python", "n8n"}


def test_job_from_row_reads_sqlite_row(isolated_db):
    from core.models import Job
    from db import get_conn, init_db, upsert_jobs

    init_db()
    upsert_jobs([{"id": "j1", "title": "T", "skills": '["python"]', "budget_type": "FIXED",
                  "budget_amount": 250}])  # fmt: skip
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = 'j1'").fetchone()
    job = Job.from_row(row)
    assert job.id == "j1"
    assert job.skills == ["python"]
    assert job.budget_amount == 250.0
    assert job.url == "https://www.upwork.com/jobs/j1"
    assert job.haystack.startswith("T ")
