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
  classifiers. Classifications survive the text purge, which makes analysis
  cache-first.
- `job_snapshots` turns a job into a time series: one row per observation of
  its applicant count (and, where the key allows, hire/invite activity).
- FTS5 virtual table `jobs_fts` provides O(log N) full-text search over
  title/description/skills — replaces Python `re` scans.
- Migrations are tracked in `schema_meta` and applied idempotently so older
  databases upgrade transparently.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from config import DB_PATH, PURGE_FIELDS, SNAPSHOT_MIN_GAP_HOURS
from taxonomies.countries import to_iso2

SCHEMA_VERSION = 6  # Bump when adding a migration to MIGRATIONS below.

CLASSIFICATION_AXES = ("industry", "workflow", "stack", "platform")
PURGEABLE_FIELDS = ("title", "description")


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


def _classifications_ddl(table: str) -> str:
    axes = ",".join(f"'{a}'" for a in CLASSIFICATION_AXES)
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    axis          TEXT NOT NULL CHECK (axis IN ({axes})),
    label         TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('regex','llm','manual')),
    confidence    REAL DEFAULT 1.0,
    classified_at TEXT NOT NULL,
    PRIMARY KEY (job_id, axis, label, source)
)"""


_BASE_SCHEMA = f"""
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
    client_total_posted    INTEGER DEFAULT 0,
    hire_rate              REAL,
    subcategory            TEXT DEFAULT '',
    purged_at              TEXT,
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

{_classifications_ddl("job_classifications")};
CREATE INDEX IF NOT EXISTS idx_class_label    ON job_classifications(axis, label);
CREATE INDEX IF NOT EXISTS idx_class_source   ON job_classifications(source);

CREATE TABLE IF NOT EXISTS job_snapshots (
    job_id           TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    observed_at      TEXT NOT NULL,
    stage            TEXT NOT NULL DEFAULT 'search',
    total_applicants INTEGER,
    hired_count      INTEGER,
    invites_sent     INTEGER,
    proposals_tier   TEXT,
    source           TEXT NOT NULL DEFAULT 'search',
    PRIMARY KEY (job_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_snap_job       ON job_snapshots(job_id, observed_at);

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


# ─── Time helpers ───────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso_precise() -> str:
    """Microsecond timestamp for observations that may repeat within a second."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse the timestamp shapes this DB holds ("…T…Z", "…T…", "… …"). UTC assumed."""
    if not value:
        return None
    s = value.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1]
    if s.endswith("+0000"):
        s = s[:-5]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def hours_between(earlier: str | None, later: str | None) -> float | None:
    a, b = parse_iso(earlier), parse_iso(later)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def _cutoff_iso(hours: float, now: str | None = None) -> str:
    base = parse_iso(now) or datetime.now(UTC)
    return (base - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Migrations ─────────────────────────────────────────────────────────────


def _legacy_columns(conn) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}


def _ensure_legacy_columns(conn):
    """Patch in any columns missing on an older `jobs` table before migrations run."""
    cols = _legacy_columns(conn)
    if not cols:
        return  # fresh DB, base schema will handle it
    legacy_adds = {
        "description": "TEXT NOT NULL DEFAULT ''",
        "url": "TEXT NOT NULL DEFAULT ''",
        "budget_min": "REAL DEFAULT 0",
        "budget_max": "REAL DEFAULT 0",
        "total_applicants": "INTEGER DEFAULT 0",
        "client_total_hires": "INTEGER DEFAULT 0",
        "client_total_spent": "REAL DEFAULT 0",
        "client_verified": "INTEGER DEFAULT 0",
        "client_feedback": "REAL DEFAULT 0",
        "client_country": "TEXT DEFAULT ''",
        "is_premium": "INTEGER DEFAULT 0",
        "is_enterprise": "INTEGER DEFAULT 0",
        "duration_label": "TEXT DEFAULT ''",
        "first_seen_at": "TEXT",
        "last_fetched_at": "TEXT",
        "fetch_count": "INTEGER DEFAULT 1",
        "discovered_via_search": "TEXT DEFAULT ''",
        # v4
        "client_total_posted": "INTEGER DEFAULT 0",
        "hire_rate": "REAL",
        "subcategory": "TEXT DEFAULT ''",
        "purged_at": "TEXT",
    }
    for col, defn in legacy_adds.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {defn}")


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
        conn.execute(
            """
            UPDATE jobs
               SET first_seen_at   = COALESCE(NULLIF(first_seen_at, ''), fetched_at, ?),
                   last_fetched_at = COALESCE(NULLIF(last_fetched_at, ''), fetched_at, ?)
             WHERE first_seen_at IS NULL OR first_seen_at = ''
                OR last_fetched_at IS NULL OR last_fetched_at = ''
        """,
            (_now_iso(), _now_iso()),
        )
    else:
        ts = _now_iso()
        conn.execute(
            """
            UPDATE jobs
               SET first_seen_at   = COALESCE(NULLIF(first_seen_at, ''), ?),
                   last_fetched_at = COALESCE(NULLIF(last_fetched_at, ''), ?)
             WHERE first_seen_at IS NULL OR first_seen_at = ''
                OR last_fetched_at IS NULL OR last_fetched_at = ''
        """,
            (ts, ts),
        )

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


def _migration_4_client_cols_and_countries(conn):
    """Columns are added by `_ensure_legacy_columns`; here we normalise data.

    - client_country → ISO 3166-1 alpha-2 (United States/USA → US, GBR/UK → GB)
    - legacy timestamps "YYYY-MM-DD HH:MM:SS" → "YYYY-MM-DDTHH:MM:SSZ" so string
      comparisons against `_now_iso()` values are always well-ordered.
    """
    rows = conn.execute(
        "SELECT DISTINCT client_country FROM jobs WHERE client_country != ''"
    ).fetchall()
    for r in rows:
        code = to_iso2(r[0])
        if code != r[0]:
            conn.execute(
                "UPDATE jobs SET client_country = ? WHERE client_country = ?", (code, r[0])
            )
    for col in ("first_seen_at", "last_fetched_at"):
        conn.execute(
            f"UPDATE jobs SET {col} = replace({col}, ' ', 'T') || 'Z' "
            f"WHERE {col} LIKE '% %' AND {col} NOT LIKE '%Z'"
        )


def _migration_5_snapshot_baseline(conn):
    """One `search` observation per existing job, dated at its last fetch."""
    conn.execute(
        """
        INSERT OR IGNORE INTO job_snapshots (job_id, observed_at, stage, total_applicants, source)
        SELECT id, last_fetched_at, 'search', total_applicants, 'search'
          FROM jobs
         WHERE last_fetched_at IS NOT NULL AND last_fetched_at != ''
        """
    )


def _migration_6_classification_axes(conn):
    """Allow the `platform` axis. SQLite cannot ALTER a CHECK, so rebuild.

    The table is a cache; the copy is exact and the indexes are recreated.
    Skipped when the live DDL already lists 'platform' (fresh databases).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_classifications'"
    ).fetchone()
    if row and "'platform'" in (row[0] or ""):
        return
    conn.execute(_classifications_ddl("job_classifications_new"))
    conn.execute(
        """
        INSERT INTO job_classifications_new
            (job_id, axis, label, source, confidence, classified_at)
        SELECT job_id, axis, label, source, confidence, classified_at
          FROM job_classifications
        """
    )
    conn.execute("DROP TABLE job_classifications")
    conn.execute("ALTER TABLE job_classifications_new RENAME TO job_classifications")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_class_label ON job_classifications(axis, label)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_class_source ON job_classifications(source)")


MIGRATIONS = [
    (
        1,
        _migration_1_backfill_tracking_cols,
        "Backfill first_seen_at / last_fetched_at / url / fetch_count",
    ),
    (2, _migration_2_normalise_skills, "Populate job_skills from JSON blobs"),
    (3, _migration_3_seed_fts, "Seed jobs_fts from existing rows"),
    (
        4,
        _migration_4_client_cols_and_countries,
        "Client posted-jobs count, hire_rate, subcategory, purged_at; ISO countries; ISO timestamps",
    ),
    (5, _migration_5_snapshot_baseline, "job_snapshots table with a baseline observation per job"),
    (6, _migration_6_classification_axes, "job_classifications accepts the 'platform' axis"),
]


# Migrations that need the FTS index to exist (they rebuild or query it).
# Every other migration runs BEFORE the FTS schema is (re)created, so their row
# updates can never fire the FTS triggers against an unseeded index.
_FTS_MIGRATIONS = {3}


def _applied_versions(conn) -> set[int]:
    try:
        return {int(r[0]) for r in conn.execute("SELECT version FROM schema_meta").fetchall()}
    except sqlite3.OperationalError:
        return set()


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _run_migration_safely(conn, version: int, fn, desc: str) -> None:
    """Run one migration inside a SAVEPOINT so a failure rolls back only it."""
    sp = f"mig_{version}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        fn(conn)
        _record_migration(conn, version, desc)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise


def init_db():
    """Create tables, run migrations, ensure FTS is in sync. Idempotent.

    Order matters: FTS triggers must NOT exist while migrations back-fill or
    rewrite `jobs` rows on a database that has no FTS index yet, otherwise the
    AFTER UPDATE trigger fires against an unseeded index and corrupts it. So
    row-level migrations run first, then the FTS schema, then FTS-dependent
    migrations. A database that gains its FTS index here is rebuilt from rows.

    Each migration runs in its own SAVEPOINT — if migration N fails after
    N-1 committed, the DB is left at version N-1 cleanly (re-runnable).
    """
    with get_conn() as conn:
        # 1) Patch older `jobs` tables so later statements never miss a column.
        _ensure_legacy_columns(conn)

        # 2) Create non-FTS schema (jobs + child tables + schema_meta).
        conn.executescript(_BASE_SCHEMA)
        fts_existed = _table_exists(conn, "jobs_fts")

        # 3) Row-level migrations, before any FTS trigger can exist on a new index.
        applied = _applied_versions(conn)
        for version, fn, desc in MIGRATIONS:
            if version not in applied and version not in _FTS_MIGRATIONS:
                _run_migration_safely(conn, version, fn, desc)

        # 4) FTS table + triggers; seed the index if this DB just gained it.
        conn.executescript(_FTS_SCHEMA)
        if not fts_existed and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
            conn.execute("INSERT INTO jobs_fts(jobs_fts) VALUES('rebuild')")

        # 5) FTS-dependent migrations.
        applied = _applied_versions(conn)
        for version, fn, desc in MIGRATIONS:
            if version not in applied and version in _FTS_MIGRATIONS:
                _run_migration_safely(conn, version, fn, desc)


# ─── Writes ─────────────────────────────────────────────────────────────────

# Every column the INSERT below binds by name. Callers may pass partial dicts
# (the demo seeder, event ingest); missing keys take these defaults instead of
# raising `ProgrammingError`.
JOB_DEFAULTS: dict = {
    "title": "",
    "description": "",
    "url": "",
    "published_at": "",
    "category": "",
    "contractor_tier": "UNKNOWN",
    "budget_type": "UNKNOWN",
    "budget_amount": 0.0,
    "budget_min": 0.0,
    "budget_max": 0.0,
    "skills": "[]",
    "total_applicants": 0,
    "client_total_hires": 0,
    "client_total_spent": 0.0,
    "client_verified": 0,
    "client_feedback": 0.0,
    "client_country": "",
    "is_premium": 0,
    "is_enterprise": 0,
    "duration_label": "",
    "client_total_posted": 0,
    "subcategory": "",
}

_UPSERT_SQL = """
    INSERT INTO jobs (
        id, title, description, url, published_at, category, contractor_tier,
        budget_type, budget_amount, budget_min, budget_max, skills,
        total_applicants, client_total_hires, client_total_spent,
        client_verified, client_feedback, client_country,
        is_premium, is_enterprise, duration_label,
        client_total_posted, hire_rate, subcategory, purged_at,
        first_seen_at, last_fetched_at, fetch_count, discovered_via_search
    ) VALUES (
        :id, :title, :description, :url, :published_at, :category, :contractor_tier,
        :budget_type, :budget_amount, :budget_min, :budget_max, :skills,
        :total_applicants, :client_total_hires, :client_total_spent,
        :client_verified, :client_feedback, :client_country,
        :is_premium, :is_enterprise, :duration_label,
        :client_total_posted, :hire_rate, :subcategory, NULL,
        :first_seen_at, :last_fetched_at, 1, :discovered_via_search
    )
    ON CONFLICT(id) DO UPDATE SET
        title               = excluded.title,
        description         = excluded.description,
        url                 = excluded.url,
        published_at        = excluded.published_at,
        category            = excluded.category,
        contractor_tier     = excluded.contractor_tier,
        budget_type         = excluded.budget_type,
        budget_amount       = excluded.budget_amount,
        budget_min          = excluded.budget_min,
        budget_max          = excluded.budget_max,
        skills              = excluded.skills,
        total_applicants    = excluded.total_applicants,
        client_total_hires  = excluded.client_total_hires,
        client_total_spent  = excluded.client_total_spent,
        client_verified     = excluded.client_verified,
        client_feedback     = excluded.client_feedback,
        client_country      = excluded.client_country,
        is_premium          = excluded.is_premium,
        is_enterprise       = excluded.is_enterprise,
        duration_label      = excluded.duration_label,
        client_total_posted = excluded.client_total_posted,
        hire_rate           = excluded.hire_rate,
        subcategory         = COALESCE(NULLIF(excluded.subcategory, ''), jobs.subcategory),
        purged_at           = NULL,        -- fresh text from the API restarts the 24h clock
        last_fetched_at     = excluded.last_fetched_at,
        fetch_count         = jobs.fetch_count + 1
        -- first_seen_at and discovered_via_search are preserved
"""


def upsert_jobs(jobs: list, search_term: str = "") -> tuple[int, int]:
    """Insert-or-update jobs. Returns (total_written, new_inserts).

    Uses a manual UPSERT so we can:
      - set `first_seen_at` only on initial insert
      - increment `fetch_count` on conflict
      - leave `discovered_via_search` untouched after first insert
      - bump `last_fetched_at` every time
      - record a `search`-stage snapshot whenever the applicant count changed
        or the last observation is older than SNAPSHOT_MIN_GAP_HOURS
    """
    if not jobs:
        return (0, 0)

    now = _now_iso()
    new_count = 0
    with get_conn() as conn:
        for raw in jobs:
            j = {**JOB_DEFAULTS, **raw}
            j["url"] = j.get("url") or f"https://www.upwork.com/jobs/{j['id']}"
            j["first_seen_at"] = now
            j["last_fetched_at"] = now
            j["discovered_via_search"] = search_term
            j["client_country"] = to_iso2(j.get("client_country"))
            posted = int(j.get("client_total_posted") or 0)
            hires = int(j.get("client_total_hires") or 0)
            j["hire_rate"] = round(min(hires / posted, 1.0), 4) if posted > 0 else None

            prev = conn.execute(
                "SELECT total_applicants FROM jobs WHERE id = ?", (j["id"],)
            ).fetchone()
            conn.execute(_UPSERT_SQL, j)

            applicants = int(j.get("total_applicants") or 0)
            observed = _now_iso_precise()
            if prev is None:
                new_count += 1
                record_snapshot(conn, j["id"], observed, applicants)
            else:
                last = conn.execute(
                    "SELECT observed_at FROM job_snapshots WHERE job_id = ? "
                    "ORDER BY observed_at DESC LIMIT 1",
                    (j["id"],),
                ).fetchone()
                changed = int(prev["total_applicants"] or 0) != applicants
                gap = hours_between(last[0], now) if last else None
                if changed or gap is None or gap >= SNAPSHOT_MIN_GAP_HOURS:
                    record_snapshot(conn, j["id"], observed, applicants)

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


def record_snapshot(
    conn,
    job_id: str,
    observed_at: str,
    total_applicants: int | None,
    *,
    stage: str = "search",
    source: str = "search",
    hired_count: int | None = None,
    invites_sent: int | None = None,
    proposals_tier: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO job_snapshots
            (job_id, observed_at, stage, total_applicants, hired_count, invites_sent,
             proposals_tier, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            observed_at,
            stage,
            total_applicants,
            hired_count,
            invites_sent,
            proposals_tier,
            source,
        ),
    )


def purge_text(
    older_than_hours: float,
    fields: tuple[str, ...] | list[str] = PURGE_FIELDS,
    *,
    dry_run: bool = False,
    now: str | None = None,
) -> int:
    """Blank fetched text on rows last fetched more than `older_than_hours` ago.

    Only `title` and `description` are purgeable; everything derived from them
    (classifications, skill tags, numbers) stays. Returns the affected row count.
    """
    cols = [f for f in fields if f in PURGEABLE_FIELDS]
    if not cols:
        return 0
    stamp = now or _now_iso()
    cutoff = _cutoff_iso(older_than_hours, stamp)
    nonempty = " OR ".join(f"{c} != ''" for c in cols)
    where = f"purged_at IS NULL AND last_fetched_at < ? AND ({nonempty})"
    with get_conn() as conn:
        if dry_run:
            return conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", (cutoff,)).fetchone()[0]
        sets = ", ".join(f"{c} = ''" for c in cols)
        cur = conn.execute(f"UPDATE jobs SET {sets}, purged_at = ? WHERE {where}", (stamp, cutoff))
        return cur.rowcount


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


def get_last_success() -> str | None:
    """ISO timestamp of the most recent completed fetch run, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(finished_at) FROM fetch_runs WHERE status = 'done'"
        ).fetchone()
    return row[0] if row and row[0] else None


def count_fetch_runs(since_iso: str, status: str = "done") -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM fetch_runs WHERE status = ? AND started_at >= ?",
            (status, since_iso),
        ).fetchone()[0]


# ─── Classification cache ───────────────────────────────────────────────────


def save_classifications(rows: list[dict]):
    """Persist classification results.

    Each row: {job_id, axis, label, source, confidence?}
    """
    if not rows:
        return
    now = _now_iso()
    payload = [
        (r["job_id"], r["axis"], r["label"], r["source"], r.get("confidence", 1.0), now)
        for r in rows
    ]
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO job_classifications
               (job_id, axis, label, source, confidence, classified_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            payload,
        )


def load_classifications(job_ids: list[str], source: str | None = None) -> dict:
    """Returns {job_id: {axis: [labels]}}."""
    if not job_ids:
        return {}
    out: dict = {}
    with get_conn() as conn:
        for chunk in _chunks(list(job_ids), 900):
            placeholders = ",".join("?" * len(chunk))
            q = f"""SELECT job_id, axis, label, source FROM job_classifications
                    WHERE job_id IN ({placeholders})"""
            params: list = list(chunk)
            if source:
                q += " AND source = ?"
                params.append(source)
            for r in conn.execute(q, params):
                out.setdefault(r["job_id"], {}).setdefault(r["axis"], []).append(r["label"])
    return out


def job_ids_with_label(axis: str, label: str, since_iso: str | None = None) -> list[str]:
    """Ids of jobs carrying a cached label, optionally published since `since_iso`."""
    sql = """
        SELECT DISTINCT c.job_id
          FROM job_classifications c
          JOIN jobs j ON j.id = c.job_id
         WHERE c.axis = ? AND c.label = ?
    """
    params: list = [axis, label]
    if since_iso:
        sql += " AND j.published_at >= ?"
        params.append(since_iso)
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql, params).fetchall()]


# ─── Reads ──────────────────────────────────────────────────────────────────


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def get_total_jobs() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def count_jobs_since(since_iso: str) -> int:
    """Jobs published on or after `since_iso` — the sample size behind a window."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE published_at >= ?", (since_iso,)
        ).fetchone()[0]


def get_date_range() -> tuple:
    with get_conn() as conn:
        row = conn.execute("SELECT MIN(published_at), MAX(published_at) FROM jobs").fetchone()
        return (row[0] or "N/A", row[1] or "N/A")


def get_jobs_by_ids(job_ids: list[str]) -> list[sqlite3.Row]:
    if not job_ids:
        return []
    rows: list[sqlite3.Row] = []
    with get_conn() as conn:
        for chunk in _chunks(list(job_ids), 900):
            placeholders = ",".join("?" * len(chunk))
            rows.extend(
                conn.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", chunk).fetchall()
            )
    return rows


def jobs_fetched_since(since_iso: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE last_fetched_at >= ? ORDER BY last_fetched_at",
            (since_iso,),
        ).fetchall()


def snapshots_for(job_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM job_snapshots WHERE job_id = ? ORDER BY observed_at", (job_id,)
        ).fetchall()


def search_jobs_fts(query: str, since_iso: str | None = None) -> list[str]:
    """Return job ids matching FTS5 query, optionally filtered to recent rows."""
    sql = """
        SELECT j.id
          FROM jobs j
          JOIN jobs_fts f ON f.rowid = j.rowid
         WHERE jobs_fts MATCH ?
    """
    params: list = [query]
    if since_iso:
        sql += " AND j.published_at >= ?"
        params.append(since_iso)
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql, params).fetchall()]
