import sqlite3
from contextlib import contextmanager

from config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id                  TEXT PRIMARY KEY,
                title               TEXT,
                published_at        TEXT,
                category            TEXT,
                contractor_tier     TEXT,
                budget_type         TEXT,
                budget_amount       REAL,
                budget_min          REAL,
                budget_max          REAL,
                skills              TEXT,
                total_applicants    INTEGER DEFAULT 0,
                client_total_hires  INTEGER DEFAULT 0,
                client_total_spent  REAL    DEFAULT 0,
                client_verified     INTEGER DEFAULT 0,
                client_feedback     REAL    DEFAULT 0,
                client_country      TEXT,
                is_premium          INTEGER DEFAULT 0,
                is_enterprise       INTEGER DEFAULT 0,
                duration_label      TEXT,
                fetched_at          TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_published ON jobs(published_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category  ON jobs(category)")

        # Migrate existing DB — add new columns if they don't exist yet
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        migrations = {
            "budget_min":         "REAL    DEFAULT 0",
            "budget_max":         "REAL    DEFAULT 0",
            "total_applicants":   "INTEGER DEFAULT 0",
            "client_total_hires": "INTEGER DEFAULT 0",
            "client_total_spent": "REAL    DEFAULT 0",
            "client_verified":    "INTEGER DEFAULT 0",
            "client_feedback":    "REAL    DEFAULT 0",
            "client_country":     "TEXT",
            "is_premium":         "INTEGER DEFAULT 0",
            "is_enterprise":      "INTEGER DEFAULT 0",
            "duration_label":     "TEXT",
        }
        for col, definition in migrations.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {definition}")

        # Add country index after migration (column now guaranteed to exist)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_country ON jobs(client_country)")


def upsert_jobs(jobs: list) -> int:
    if not jobs:
        return 0
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO jobs (
                id, title, published_at, category, contractor_tier,
                budget_type, budget_amount, budget_min, budget_max, skills,
                total_applicants, client_total_hires, client_total_spent,
                client_verified, client_feedback, client_country,
                is_premium, is_enterprise, duration_label
            ) VALUES (
                :id, :title, :published_at, :category, :contractor_tier,
                :budget_type, :budget_amount, :budget_min, :budget_max, :skills,
                :total_applicants, :client_total_hires, :client_total_spent,
                :client_verified, :client_feedback, :client_country,
                :is_premium, :is_enterprise, :duration_label
            )
            """,
            jobs,
        )
    return len(jobs)


def get_total_jobs() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def get_date_range() -> tuple:
    with get_conn() as conn:
        row = conn.execute("SELECT MIN(published_at), MAX(published_at) FROM jobs").fetchone()
        return (row[0] or "N/A", row[1] or "N/A")
