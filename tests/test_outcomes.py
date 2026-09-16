"""Manual outcomes: DB row + JSONL ledger, URL resolution, bulk CSV, idempotency."""

from __future__ import annotations

import json


def test_record_outcome_writes_db_and_ledger(isolated_db, tmp_path):
    import db
    from features.funnel.outcomes import record_outcome

    db.init_db()
    ev, n = record_outcome(
        "1000000000000000001", "submitted", bid_amount=45, bid_type="hourly", connects=16,
        note="sent from phone", ts="2026-09-16T10:00:00Z", outcomes_dir=tmp_path,
    )  # fmt: skip
    assert n == 1 and ev["event"] == "SUBMITTED" and ev["source"] == "manual"
    rows = db.events_for_job("1000000000000000001")
    assert [r["event"] for r in rows] == ["SUBMITTED"]
    assert rows[0]["bid_amount"] == 45.0 and rows[0]["connects_spent"] == 16

    ledger = tmp_path / "outcomes-202609.jsonl"
    line = json.loads(ledger.read_text().splitlines()[0])
    assert line["event_id"] == ev["event_id"] and line["meta"]["note"] == "sent from phone"

    # Same job/event/minute again → idempotent (still appended to the ledger for audit).
    _, again = record_outcome(
        "1000000000000000001", "submitted", ts="2026-09-16T10:00:30Z", outcomes_dir=tmp_path
    )
    assert again == 0


def test_url_resolves_to_ledger_job_id(isolated_db, tmp_path):
    import db
    from features.funnel.outcomes import record_outcome

    db.init_db()
    db.insert_events(
        [
            {"event_id": "n1", "job_id": "1000000000000000009", "event": "NOTIFIED",
             "ts": "2026-09-01T00:00:00Z", "source": "agent_csv",
             "meta": {"url": "https://www.upwork.com/jobs/~0100000000000009"}}
        ]
    )  # fmt: skip
    ev, _ = record_outcome(
        "https://www.upwork.com/jobs/~0100000000000009", "hired", outcomes_dir=tmp_path
    )
    assert ev["job_id"] == "1000000000000000009"
    ev2, _ = record_outcome(
        "~0100000000000042", "viewed", outcomes_dir=tmp_path
    )  # unknown → kept as given
    assert ev2["job_id"] == "~0100000000000042"


def test_unknown_event_is_rejected(isolated_db, tmp_path):
    import pytest

    import db
    from features.funnel.outcomes import record_outcome

    db.init_db()
    with pytest.raises(ValueError):
        record_outcome("1000000000000000001", "ghosted", outcomes_dir=tmp_path)


def test_bulk_csv(isolated_db, tmp_path):
    import db
    from features.funnel.outcomes import outcomes_from_csv, record_outcomes

    db.init_db()
    csv_path = tmp_path / "catchup.csv"
    csv_path.write_text(
        "job,event,ts,bid_amount,bid_type,connects,note\n"
        "1000000000000000001,submitted,2026-08-01T10:00:00Z,40,hourly,12,\n"
        "1000000000000000001,interview,2026-08-03T10:00:00Z,,,,call booked\n"
        "1000000000000000002,submitted,,600,fixed,,\n"
        ",,,,,,\n"
    )
    events = outcomes_from_csv(csv_path)
    assert [e["event"] for e in events] == ["SUBMITTED", "INTERVIEW", "SUBMITTED"]
    assert record_outcomes(events, outcomes_dir=tmp_path) == 3
    assert len(db.events_for_job("1000000000000000001")) == 2
