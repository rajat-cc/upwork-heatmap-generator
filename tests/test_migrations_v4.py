"""Upgrading a schema-v3 database (the shape of the real upwork_jobs.db) must:
add the new columns, normalise countries and timestamps, seed baseline snapshots,
rebuild the classification table to accept the platform axis, and stay idempotent.
"""

from __future__ import annotations

import sqlite3

_LEGACY_V3 = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '', published_at TEXT, category TEXT, contractor_tier TEXT,
    budget_type TEXT, budget_amount REAL DEFAULT 0, budget_min REAL DEFAULT 0,
    budget_max REAL DEFAULT 0, skills TEXT NOT NULL DEFAULT '[]', total_applicants INTEGER DEFAULT 0,
    client_total_hires INTEGER DEFAULT 0, client_total_spent REAL DEFAULT 0,
    client_verified INTEGER DEFAULT 0, client_feedback REAL DEFAULT 0, client_country TEXT DEFAULT '',
    is_premium INTEGER DEFAULT 0, is_enterprise INTEGER DEFAULT 0, duration_label TEXT DEFAULT '',
    first_seen_at TEXT NOT NULL, last_fetched_at TEXT NOT NULL, fetch_count INTEGER NOT NULL DEFAULT 1,
    discovered_via_search TEXT NOT NULL DEFAULT ''
);
CREATE TABLE job_skills (job_id TEXT NOT NULL, skill TEXT NOT NULL, PRIMARY KEY (job_id, skill));
CREATE TABLE job_classifications (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    axis TEXT NOT NULL CHECK (axis IN ('industry','workflow','stack')),
    label TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('regex','llm','manual')),
    confidence REAL DEFAULT 1.0, classified_at TEXT NOT NULL,
    PRIMARY KEY (job_id, axis, label, source)
);
CREATE TABLE fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, search_term TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT,
    jobs_seen INTEGER DEFAULT 0, jobs_new INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE schema_meta (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT);
INSERT INTO schema_meta VALUES (1, '2026-05-04T07:11:38Z', 'v1'), (2, '2026-05-04T07:11:38Z', 'v2'),
                               (3, '2026-05-04T07:11:38Z', 'v3');
INSERT INTO jobs (id, title, description, published_at, client_country, total_applicants,
                  first_seen_at, last_fetched_at)
VALUES ('a', 'n8n job', 'text', '2026-04-24T10:00:00', 'USA', 12,
        '2026-04-24 06:18:14', '2026-05-02 13:09:36'),
       ('b', 'other', 'text', '2026-04-25T10:00:00', 'United Kingdom', 3,
        '2026-04-25 06:18:14', '2026-05-02 13:09:36');
INSERT INTO job_classifications VALUES ('a', 'workflow', 'CRM Sync', 'regex', 1.0, '2026-05-04T07:11:38Z');
"""


def _make_legacy(path):
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_V3)
    conn.commit()
    conn.close()


def _cols(path, table):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_v3_database_upgrades_cleanly(isolated_db):
    _make_legacy(isolated_db)
    from db import MIGRATIONS, init_db

    init_db()

    assert {"client_total_posted", "hire_rate", "subcategory", "purged_at"} <= _cols(
        isolated_db, "jobs"
    )
    with sqlite3.connect(isolated_db) as conn:
        versions = sorted(r[0] for r in conn.execute("SELECT version FROM schema_meta"))
        countries = dict(conn.execute("SELECT id, client_country FROM jobs").fetchall())
        stamps = dict(conn.execute("SELECT id, first_seen_at FROM jobs").fetchall())
        snaps = conn.execute(
            "SELECT job_id, total_applicants FROM job_snapshots ORDER BY job_id"
        ).fetchall()
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_classifications'"
        ).fetchone()[0]
        kept = conn.execute("SELECT job_id, axis, label FROM job_classifications").fetchall()
        # The rebuilt table accepts the new axis.
        conn.execute(
            "INSERT INTO job_classifications VALUES ('a', 'platform', 'n8n', 'regex', 1.0, 'now')"
        )
    assert versions == [v for v, _, _ in MIGRATIONS]
    assert countries == {"a": "US", "b": "GB"}
    assert stamps["a"] == "2026-04-24T06:18:14Z"
    assert snaps == [("a", 12), ("b", 3)]
    assert "'platform'" in ddl
    assert kept == [("a", "workflow", "CRM Sync")]


def test_upgrade_is_idempotent(isolated_db):
    _make_legacy(isolated_db)
    from db import init_db

    init_db()
    init_db()
    with sqlite3.connect(isolated_db) as conn:
        counts = conn.execute(
            "SELECT version, COUNT(*) FROM schema_meta GROUP BY version"
        ).fetchall()
        snaps = conn.execute("SELECT COUNT(*) FROM job_snapshots").fetchone()[0]
    assert all(c == 1 for _, c in counts)
    assert snaps == 2  # baseline is not duplicated
