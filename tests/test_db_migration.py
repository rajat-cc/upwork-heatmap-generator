"""Migration must be idempotent and self-healing on a fresh DB."""

import sqlite3


def test_init_db_creates_all_tables(isolated_db):
    from db import init_db

    init_db()

    with sqlite3.connect(isolated_db) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    expected = {
        "jobs",
        "job_skills",
        "job_classifications",
        "fetch_runs",
        "schema_meta",
        "jobs_fts",
    }
    missing = expected - tables
    assert not missing, f"Missing tables after init_db: {missing}"


def test_init_db_records_all_migrations(isolated_db):
    from db import MIGRATIONS, init_db

    init_db()

    with sqlite3.connect(isolated_db) as conn:
        applied = sorted(r[0] for r in conn.execute("SELECT version FROM schema_meta").fetchall())

    expected = sorted(v for v, _, _ in MIGRATIONS)
    assert applied == expected


def test_init_db_is_idempotent(isolated_db):
    """Re-running init_db on a migrated DB must not duplicate migrations."""
    from db import init_db

    init_db()
    init_db()  # second call — should be a no-op

    with sqlite3.connect(isolated_db) as conn:
        rows = conn.execute("SELECT version, COUNT(*) FROM schema_meta GROUP BY version").fetchall()

    for version, count in rows:
        assert count == 1, f"Migration v{version} ran {count} times (should be 1)"


def test_fts5_search_works_after_init(isolated_db):
    """FTS5 must be queryable on a fresh empty DB without errors."""
    from db import init_db, search_jobs_fts

    init_db()
    # Empty DB → empty results, but no error.
    assert search_jobs_fts("n8n") == []


def test_indexes_present(isolated_db):
    from db import init_db

    init_db()

    with sqlite3.connect(isolated_db) as conn:
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    expected = {
        "idx_published",
        "idx_category",
        "idx_country",
        "idx_first_seen",
        "idx_last_fetched",
        "idx_disc_search",
        "idx_skill_name",
        "idx_class_label",
        "idx_class_source",
        "idx_runs_started",
    }
    missing = expected - indexes
    assert not missing, f"Missing indexes after init_db: {missing}"
