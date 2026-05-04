"""SQLite layer for Upwork demand analysis.

Design notes:
- One canonical `jobs` table, deduped on Upwork job id.
- `first_seen_at` is set once on first insert and NEVER overwritten — this
  preserves cohort/recency semantics across re-fetches.
- `last_fetched_at` and `fetch_count` track refresh history.
- `discovered_via_search` records the search term that originally surfaced
  the job (set once, never overwritten).
- Child tables `job_skills` and `job_classifications` normalise skill and
  taxonomy data so we can query/cache without re-parsing JSON or re-running
  classifiers.
- FTS5 virtual table `jobs_fts` provides O(log N) full-text search over
  title/description/skills — replaces Python `re` scans.
- Migrations are tracked in `schema_meta` and applied idempotently so older
  databases upgrade transparently.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA_VERSION = 3  # Bump when adding a migration to MIGRATIONS below.


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Schema definition ──────────────────────────────────────────────────────

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                     TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    description            TEXT NOT NULL DEFAULT '',
    url                    TEXT NOT NULL DEFAULT '',
    published_at           TEXT,
    category               TEXT,
    contractor_tier        TEXT
                           CHECK (contractor_tier IN
                                  ('ENTRY_LEVEL','INTERMEDIATE','EXPERT','UNKNOWN','')),
    budget_type            TEXT
                           CHECK (budget_type IN ('HOURLY','FIXED','UNKNOWN','')),
    budget_amount          REAL DEFAULT 0,
    budget_min             REAL DEFAULT 0,
    budget_max             REAL DEFAULT 0,
    skills                 TEXT NOT NULL DEFAULT '[]',
    total_applicants       INTEGER DEFAULT 0,
    client_total_hires     INTEGER DEFAULT 0,
    client_total_spent     REAL    DEFAULT 0,
    client_verified        INTEGER DEFAULT 0,
    client_feedback        REAL    DEFAULT 0,
    client_country         TEXT DEFAULT '',
    is_premium             INTEGER DEFAULT 0,
    is_enterprise          INTEGER DEFAULT 0,
    duration_label         TEXT DEFAULT '',
    first_seen_at          TEXT NOT NULL,
    last_fetched_at        TEXT NOT NULL,
    fetch_count            INTEGER NOT NULL DEFAULT 1,
    discovered_via_search  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_published      ON jobs(published_at);
CREATE INDEX IF NOT EXISTS idx_category       ON jobs(category);
CREATE INDEX IF NOT EXISTS idx_country        ON jobs(client_country);
CREATE INDEX IF NOT EXISTS idx_first_seen     ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_last_fetched   ON jobs(last_fetched_at);
CREATE INDEX IF NOT EXISTS idx_disc_search    ON jobs(discovered_via_search);

CREATE TABLE IF NOT EXISTS job_skills (
    job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    skill   TEXT NOT NULL,
    PRIMARY KEY (job_id, skill)
);
CREATE INDEX IF NOT EXISTS idx_skill_name     ON job_skills(skill);

CREATE TABLE IF NOT EXISTS job_classifications (
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    axis          TEXT NOT NULL CHECK (axis IN ('industry','workflow','stack')),
    label         TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('regex','llm','manual')),
    confidence    REAL DEFAULT 1.0,
    classified_at TEXT NOT NULL,
    PRIMARY KEY (job_id, axis, label, source)
);
CREATE INDEX IF NOT EXISTS idx_class_label    ON job_classifications(axis, label);
CREATE INDEX IF NOT EXISTS idx_class_source   ON job_classifications(source);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term  TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    jobs_seen    INTEGER DEFAULT 0,
    jobs_new     INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','done','error'))
);
CREATE INDEX IF NOT EXISTS idx_runs_started   ON fetch_runs(started_at);

CREATE TABLE IF NOT EXISTS schema_meta (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
"""

# FTS5 + triggers — kept separate because contentless FTS needs special handling.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title, description, skills,
    content='jobs', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS jobs_ai AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts(rowid, title, description, skills)
        VALUES (new.rowid, new.title, new.description, new.skills);
END;

CREATE TRIGGER IF NOT EXISTS jobs_ad AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description, skills)
        VALUES('delete', old.rowid, old.title, old.description, old.skills);
END;

CREATE TRIGGER IF NOT EXISTS jobs_au AFTER UPDATE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description, skills)
        VALUES('delete', old.rowid, old.title, old.description, old.skills);
    INSERT INTO jobs_fts(rowid, title, description, skills)
        VALUES (new.rowid, new.title, new.description, new.skills);
END;
"""


# ─── Migrations ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _legacy_columns(conn) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}


def _ensure_legacy_columns(conn):
    """Patch in any columns missing on a pre-v1 schema before running migrations."""
    cols = _legacy_columns(conn)
    if not cols:
        return  # fresh DB, base schema will handle it
    legacy_adds = {
        "description":            "TEXT NOT NULL DEFAULT ''",
        "url":                    "TEXT NOT NULL DEFAULT ''",
        "budget_min":             "REAL DEFAULT 0",
        "budget_max":             "REAL DEFAULT 0",
        "total_applicants":       "INTEGER DEFAULT 0",
        "client_total_hires":     "INTEGER DEFAULT 0",
        "client_total_spent":     "REAL DEFAULT 0",
        "client_verified":        "INTEGER DEFAULT 0",
        "client_feedback":        "REAL DEFAULT 0",
        "client_country":         "TEXT DEFAULT ''",
        "is_premium":             "INTEGER DEFAULT 0",
        "is_enterprise":          "INTEGER DEFAULT 0",
        "duration_label":         "TEXT DEFAULT ''",
        "first_seen_at":          "TEXT",
        "last_fetched_at":        "TEXT",
        "fetch_count":            "INTEGER DEFAULT 1",
        "discovered_via_search":  "TEXT DEFAULT ''",
    }
    for col, defn in legacy_adds.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")


def _current_version(conn) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_meta").fetchone()
        return int(row[0] or 0)
    except sqlite3.OperationalError:
        return 0


def _record_migration(conn, version: int, description: str):
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (version, applied_at, description) VALUES (?, ?, ?)",
        (version, _now_iso(), description),
    )


# Each migration runs idempotently and once per version.
# New migrations: add a function and a (version, callable, description) entry.

def _migration_1_backfill_tracking_cols(conn):
    """Backfill first_seen_at / last_fetched_at / url / fetch_count for rows
    that pre-date the new schema (or were inserted from a legacy fetched_at)."""
    legacy_fetched = "fetched_at" in _legacy_columns(conn)
    if legacy_fetched:
        # Use the old fetched_at as both first_seen_at and last_fetched_at.
        conn.execute("""
            UPDATE jobs
               SET first_seen_at   = COALESCE(NULLIF(first_seen_at, ''), fetched_at, ?),
                   last_fetched_at = COALESCE(NULLIF(last_fetched_at, ''), fetched_at, ?)
             WHERE first_seen_at IS NULL OR first_seen_at = ''
                OR last_fetched_at IS NULL OR last_fetched_at = ''
        """, (_now_iso(), _now_iso()))
    else:
        ts = _now_iso()
        conn.execute("""
            UPDATE jobs
               SET first_seen_at   = COALESCE(NULLIF(first_seen_at, ''), ?),
                   last_fetched_at = COALESCE(NULLIF(last_fetched_at, ''), ?)
             WHERE first_seen_at IS NULL OR first_seen_at = ''
                OR last_fetched_at IS NULL OR last_fetched_at = ''
        """, (ts, ts))

    # Backfill url for rows missing it.
    conn.execute("""
        UPDATE jobs
           SET url = 'https://www.upwork.com/jobs/' || id
         WHERE url IS NULL OR url = ''
    """)

    # Default fetch_count to 1 if NULL (legacy rows).
    conn.execute("UPDATE jobs SET fetch_count = 1 WHERE fetch_count IS NULL")


def _migration_2_normalise_skills(conn):
    """Populate `job_skills` from existing `jobs.skills` JSON blobs."""
    rows = conn.execute("SELECT id, skills FROM jobs").fetchall()
    payload = []
    for r in rows:
        try:
            for sk in json.loads(r["skills"] or "[]"):
                if isinstance(sk, str) and sk.strip():
                    payload.append((r["id"], sk.strip()))
        except (ValueError, TypeError):
            continue
    if payload:
        conn.executemany(
            "INSERT OR IGNORE INTO job_skills (job_id, skill) VALUES (?, ?)",
            payload,
        )


def _migration_3_seed_fts(conn):
    """Rebuild FTS5 from existing rows. Cheap on small DBs."""
    conn.execute("INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')")


MIGRATIONS = [
    (1, _migration_1_backfill_tracking_cols,
     "Backfill first_seen_at / last_fetched_at / url / fetch_count"),
    (2, _migration_2_normalise_skills,
     "Populate job_skills from JSON blobs"),
    (3, _migration_3_seed_fts,
     "Seed jobs_fts from existing rows"),
]


def init_db():
    """Create tables, run migrations, ensure FTS is in sync. Idempotent.

    Order matters: FTS triggers must NOT exist while we're back-filling old
    rows, otherwise the AFTER UPDATE trigger fires against an unseeded FTS
    index and corrupts it.
    """
    with get_conn() as conn:
        # 1) Patch legacy schema so ALTER TABLE doesn't fail on missing cols.
        _ensure_legacy_columns(conn)

        # 2) Create non-FTS schema (jobs + child tables + schema_meta).
        conn.executescript(_BASE_SCHEMA)

        # 3) Run any pending migrations that touch jobs rows BEFORE FTS exists.
        applied = _current_version(conn)
        for version, fn, desc in MIGRATIONS:
            if version > applied and version < 3:   # FTS-related migrations are >=3
                fn(conn)
                _record_migration(conn, version, desc)

        # 4) Now create FTS table + triggers (table is empty until rebuild).
        conn.executescript(_FTS_SCHEMA)

        # 5) Run FTS-dependent migrations (seed/rebuild).
        applied = _current_version(conn)
        for version, fn, desc in MIGRATIONS:
            if version > applied and version >= 3:
                fn(conn)
                _record_migration(conn, version, desc)


# ─── Writes ─────────────────────────────────────────────────────────────────

def upsert_jobs(jobs: list, search_term: str = "") -> tuple[int, int]:
    """Insert-or-update jobs. Returns (total_written, new_inserts).

    Uses a manual UPSERT so we can:
      - set `first_seen_at` only on initial insert
      - increment `fetch_count` on conflict
      - leave `discovered_via_search` untouched after first insert
      - bump `last_fetched_at` every time
    """
    if not jobs:
        return (0, 0)

    now = _now_iso()
    new_count = 0
    with get_conn() as conn:
        for j in jobs:
            j["url"] = j.get("url") or f"https://www.upwork.com/jobs/{j['id']}"
            j["first_seen_at"] = now
            j["last_fetched_at"] = now
            j["discovered_via_search"] = search_term

            cur = conn.execute("""
                INSERT INTO jobs (
                    id, title, description, url, published_at, category, contractor_tier,
                    budget_type, budget_amount, budget_min, budget_max, skills,
                    total_applicants, client_total_hires, client_total_spent,
                    client_verified, client_feedback, client_country,
                    is_premium, is_enterprise, duration_label,
                    first_seen_at, last_fetched_at, fetch_count, discovered_via_search
                ) VALUES (
                    :id, :title, :description, :url, :published_at, :category, :contractor_tier,
                    :budget_type, :budget_amount, :budget_min, :budget_max, :skills,
                    :total_applicants, :client_total_hires, :client_total_spent,
                    :client_verified, :client_feedback, :client_country,
                    :is_premium, :is_enterprise, :duration_label,
                    :first_seen_at, :last_fetched_at, 1, :discovered_via_search
                )
                ON CONFLICT(id) DO UPDATE SET
                    title              = excluded.title,
                    description        = excluded.description,
                    url                = excluded.url,
                    published_at       = excluded.published_at,
                    category           = excluded.category,
                    contractor_tier    = excluded.contractor_tier,
                    budget_type        = excluded.budget_type,
                    budget_amount      = excluded.budget_amount,
                    budget_min         = excluded.budget_min,
                    budget_max         = excluded.budget_max,
                    skills             = excluded.skills,
                    total_applicants   = excluded.total_applicants,
                    client_total_hires = excluded.client_total_hires,
                    client_total_spent = excluded.client_total_spent,
                    client_verified    = excluded.client_verified,
                    client_feedback    = excluded.client_feedback,
                    client_country     = excluded.client_country,
                    is_premium         = excluded.is_premium,
                    is_enterprise      = excluded.is_enterprise,
                    duration_label     = excluded.duration_label,
                    last_fetched_at    = excluded.last_fetched_at,
                    fetch_count        = jobs.fetch_count + 1
                    -- first_seen_at and discovered_via_search are preserved
            """, j)

            # cur.rowcount is unreliable for ON CONFLICT; check via lastrowid changes
            # Simpler: detect new vs update by checking fetch_count after.
            row = conn.execute(
                "SELECT fetch_count FROM jobs WHERE id = ?", (j["id"],)
            ).fetchone()
            if row and row[0] == 1:
                new_count += 1

            # Mirror skills into normalized table.
            try:
                skills = json.loads(j.get("skills") or "[]")
            except (ValueError, TypeError):
                skills = []
            conn.execute("DELETE FROM job_skills WHERE job_id = ?", (j["id"],))
            if skills:
                conn.executemany(
                    "INSERT OR IGNORE INTO job_skills (job_id, skill) VALUES (?, ?)",
                    [(j["id"], s.strip()) for s in skills if isinstance(s, str) and s.strip()],
                )

    return (len(jobs), new_count)


# ─── Fetch-run observability ────────────────────────────────────────────────

def start_fetch_run(search_term: str = "", category: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fetch_runs (search_term, category, started_at, status) VALUES (?, ?, ?, 'running')",
            (search_term, category, _now_iso()),
        )
        return cur.lastrowid


def finish_fetch_run(run_id: int, jobs_seen: int, jobs_new: int, status: str = "done"):
    with get_conn() as conn:
        conn.execute(
            "UPDATE fetch_runs SET finished_at = ?, jobs_seen = ?, jobs_new = ?, status = ? WHERE id = ?",
            (_now_iso(), jobs_seen, jobs_new, status, run_id),
        )


# ─── Classification cache ───────────────────────────────────────────────────

def save_classifications(rows: list[dict]):
    """Persist classification results.

    Each row: {job_id, axis, label, source, confidence?}
    """
    if not rows:
        return
    now = _now_iso()
    payload = [
        (r["job_id"], r["axis"], r["label"], r["source"],
         r.get("confidence", 1.0), now)
        for r in rows
    ]
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO job_classifications
               (job_id, axis, label, source, confidence, classified_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            payload,
        )


def load_classifications(job_ids: list[str], source: str = None) -> dict:
    """Returns {job_id: {axis: [labels]}}."""
    if not job_ids:
        return {}
    placeholders = ",".join("?" * len(job_ids))
    q = f"""SELECT job_id, axis, label, source FROM job_classifications
            WHERE job_id IN ({placeholders})"""
    params = list(job_ids)
    if source:
        q += " AND source = ?"
        params.append(source)
    out = {}
    with get_conn() as conn:
        for r in conn.execute(q, params):
            out.setdefault(r["job_id"], {}).setdefault(r["axis"], []).append(r["label"])
    return out


# ─── Reads ──────────────────────────────────────────────────────────────────

def get_total_jobs() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def get_date_range() -> tuple:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(published_at), MAX(published_at) FROM jobs"
        ).fetchone()
        return (row[0] or "N/A", row[1] or "N/A")


def search_jobs_fts(query: str, since_iso: str = None) -> list[str]:
    """Return job ids matching FTS5 query, optionally filtered to recent rows."""
    sql = """
        SELECT j.id
          FROM jobs j
          JOIN jobs_fts f ON f.rowid = j.rowid
         WHERE jobs_fts MATCH ?
    """
    params = [query]
    if since_iso:
        sql += " AND j.published_at >= ?"
        params.append(since_iso)
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql, params).fetchall()]
