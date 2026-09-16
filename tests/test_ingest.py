"""Ingest: position-tolerant CSV, agent.db drafts/accepts, JSONL replay, idempotency."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "agent_alerts_sample.csv"


def _agent_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (job_id TEXT PRIMARY KEY, ciphertext TEXT, title TEXT, category TEXT,
            budget_label TEXT, status TEXT NOT NULL, created_date_time TEXT, first_seen_ts REAL,
            notified_ts REAL, decided_ts REAL, content_expires_ts REAL, draft TEXT,
            drafted_ts REAL, job_json TEXT);
        CREATE TABLE proposal_versions (id INTEGER PRIMARY KEY, job_id TEXT, version INTEGER,
            kind TEXT, text TEXT, created_ts REAL);
        INSERT INTO jobs (job_id, title, category, budget_label, status, decided_ts, drafted_ts)
            VALUES ('1000000000000000001', 'n8n voice agent for a dental clinic',
                    'web_mobile_software_dev', '$40–$70/hr', 'ACCEPTED', 1782297017.7, 1782296974.8),
                   ('1000000000000000005', NULL, 'web_mobile_software_dev', '$45/hr', 'DRAFTED',
                    NULL, NULL),
                   ('1000000000000000004', 'Python ETL', 'data_science_analytics', NULL,
                    'NOTIFIED', NULL, NULL);
        INSERT INTO proposal_versions (job_id, version, kind, text, created_ts)
            VALUES ('1000000000000000005', 1, 'generated', 'draft', 1782289617.3),
                   ('1000000000000000005', 2, 'edited', 'draft2', 1782289700.0);
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize(
    "label, expected",
    [
        ("$10–$25/hr", ("HOURLY", 10.0, 25.0, 17.5)),
        ("$40-$70/hr", ("HOURLY", 40.0, 70.0, 55.0)),
        ("$45/hr", ("HOURLY", 45.0, 45.0, 45.0)),
        ("$1,200 fixed", ("FIXED", 1200.0, 1200.0, 1200.0)),
        ("Budget not specified", ("UNKNOWN", None, None, None)),
        ("", ("UNKNOWN", None, None, None)),
    ],
)
def test_parse_budget(label, expected):
    from features.funnel.ingest import parse_budget

    b = parse_budget(label)
    assert (b["budget_type"], b["budget_min"], b["budget_max"], b["budget_mid"]) == expected


def test_csv_rows_are_mapped_by_their_own_width():
    from features.funnel.ingest import read_alerts_csv

    rows, skipped = read_alerts_csv(FIXTURE_CSV)
    assert skipped == 1  # the "bad,row" line
    by_id = {r["job_id"]: r for r in rows}
    assert by_id["1000000000000000001"].get("matched") is None  # 13-column row
    assert by_id["1000000000000000003"]["matched"] == "US Based"  # 14-column row
    assert by_id["1000000000000000003"]["budget"] == "$600 fixed"
    assert by_id["1000000000000000005"]["experience"] == ""


def test_ingest_is_idempotent_and_reads_every_source(isolated_db, tmp_path):
    import db
    from features.funnel.ingest import ingest

    db.init_db()
    agent = tmp_path / "agent"
    (agent / "data").mkdir(parents=True)
    _agent_db(agent / "data" / "agent.db")
    (agent / "data" / "alerts.csv").write_text(FIXTURE_CSV.read_text())
    outcomes = tmp_path / "outcomes"
    outcomes.mkdir()
    (outcomes / "outcomes-202607.jsonl").write_text(
        '{"event_id": "manual-1", "job_id": "1000000000000000001", "event": "SUBMITTED", '
        '"ts": "2026-07-01T10:00:00Z", "source": "manual", "bid_amount": 55, "bid_type": "hourly", '
        '"connects_spent": 16, "meta": {"note": "sent"}}\n'
        "not json\n"
    )

    first = ingest(str(agent), outcomes_dir=outcomes)
    assert first.sources["alerts_csv"]["seen"] == 6  # 5 valid + 1 duplicate NOTIFIED
    assert first.sources["alerts_csv"]["skipped_rows"] == 1
    assert (
        first.sources["agent_db"]["seen"] == 3
    )  # DRAFTED+ACCEPTED for one job, DRAFTED for another
    assert first.sources["outcomes_jsonl"]["seen"] == 1
    assert first.inserted == 9  # the duplicate NOTIFIED row shares its minute → same id

    second = ingest(str(agent), outcomes_dir=outcomes)
    assert second.inserted == 0 and second.seen == first.seen

    with db.get_conn() as conn:
        by_event = dict(conn.execute("SELECT event, COUNT(*) FROM proposal_events GROUP BY event"))
        drafted = conn.execute(
            "SELECT ts FROM proposal_events WHERE event = 'DRAFTED' AND job_id = '1000000000000000005'"
        ).fetchone()[0]
        notified_meta = conn.execute(
            "SELECT meta_json FROM proposal_events WHERE event='NOTIFIED' AND job_id='1000000000000000001'"
        ).fetchone()[0]
    assert by_event == {"NOTIFIED": 4, "FILTERED": 1, "DRAFTED": 2, "ACCEPTED": 1, "SUBMITTED": 1}
    assert drafted == "2026-06-24T08:26:57Z"  # first proposal version, drafted_ts was NULL
    assert '"budget_type": "HOURLY"' in notified_meta
    assert '"Healthcare"' in notified_meta and '"n8n"' in notified_meta  # title-only labels


def test_missing_sources_are_reported_not_raised(isolated_db, tmp_path):
    import db
    from features.funnel.ingest import ingest

    db.init_db()
    result = ingest(str(tmp_path / "nowhere"), outcomes_dir=tmp_path / "out")
    assert result.inserted == 0
    assert result.sources["alerts_csv"]["missing"] and result.sources["agent_db"]["missing"]
    assert (tmp_path / "out").is_dir()  # ledger directory prepared for manual outcomes
