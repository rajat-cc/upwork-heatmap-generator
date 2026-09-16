"""Bands: cells roll up to workflow and budget-type levels; bids are placed against them."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _seed(db, n_per_wf=10):
    """Two workflows, hourly jobs with a spread; one fixed job (too few for its own band)."""
    from features.n8n import classifier

    rows = []
    for wf, base in (("CRM Sync", 30), ("Voice / Telephony", 60)):
        title = "hubspot crm sync" if wf == "CRM Sync" else "vapi voice agent phone call"
        for i in range(n_per_wf):
            rows.append(
                {"id": f"{wf[:3]}-{i}", "title": title, "published_at": _today(),
                 "budget_type": "HOURLY", "budget_min": base + i, "budget_max": base + i + 10,
                 "budget_amount": base + i + 5, "contractor_tier": "EXPERT", "client_verified": 1,
                 "client_total_hires": 3, "client_total_spent": 1000}
            )  # fmt: skip
    rows.append({"id": "fixed-1", "title": "hubspot crm sync", "published_at": _today(),
                 "budget_type": "FIXED", "budget_amount": 900, "budget_min": 900, "budget_max": 900})  # fmt: skip
    db.upsert_jobs(rows)
    classifier.persist(classifier.classify_rows(db.get_jobs_by_ids([r["id"] for r in rows])))


def test_bands_roll_up_and_bid_positions(isolated_db):
    import db
    from features.bands.analyzer import ALL, analyze, lookup

    db.init_db()
    _seed(db)
    db.insert_events(
        [
            {"event_id": "s1", "job_id": "CRM-1", "event": "SUBMITTED", "ts": _today() + "Z",
             "source": "manual", "bid_amount": 20, "bid_type": "hourly", "meta": {}},
            {"event_id": "s2", "job_id": "Voi-1", "event": "SUBMITTED", "ts": _today() + "Z",
             "source": "manual", "bid_amount": 70, "bid_type": "hourly",
             "meta": {"verified": True, "client_hires": 3, "client_spend": 1000, "experience": "Expert",
                      "labels": {"workflow": ["Voice / Telephony"]}}},
            {"event_id": "h2", "job_id": "Voi-1", "event": "HIRED", "ts": _today() + "Z", "source": "manual", "meta": {}},
            {"event_id": "s3", "job_id": "nobody", "event": "SUBMITTED", "ts": _today() + "Z",
             "source": "manual", "bid_amount": 500, "bid_type": "fixed", "meta": {}},
        ]
    )  # fmt: skip

    r = analyze(days=7, min_n=8)
    levels = {
        (row.workflow, row.client_segment, row.experience, row.budget_type): row for row in r.rows
    }
    assert ("CRM Sync", "active", "expert", "HOURLY") in levels  # a full cell (10 jobs)
    assert ("CRM Sync", ALL, ALL, "HOURLY") in levels
    assert (ALL, ALL, ALL, "HOURLY") in levels
    assert not any(row.budget_type == "FIXED" for row in r.rows)  # one fixed job < min_n
    crm = levels[("CRM Sync", "active", "expert", "HOURLY")]
    assert crm.n == 10 and crm.p25 < crm.p50 < crm.p75 and 35 <= crm.p50 <= 45
    assert lookup(r.by_key, "CRM Sync", "risky", "entry", "HOURLY").level == "workflow"  # rolled up
    assert lookup(r.by_key, "Unknown WF", "risky", "entry", "HOURLY").level == "budget_type"
    assert lookup(r.by_key, "x", "y", "z", "FIXED") is None

    bids = {b.job_id: b for b in r.bids}
    assert bids["CRM-1"].position == "below" and bids["CRM-1"].workflow == "CRM Sync"  # from cache
    assert bids["Voi-1"].position == "in" and bids["Voi-1"].outcome == "hired"
    assert bids["Voi-1"].band_level == "cell"
    assert bids["nobody"].position == "no band"


def test_empty_window(isolated_db):
    import db
    from features.bands.analyzer import analyze

    db.init_db()
    r = analyze(days=7)
    assert r.rows == [] and r.bids == [] and r.jobs == 0


def test_intel_document_is_valid_and_aggregate_only(isolated_db, tmp_path, monkeypatch):
    import db
    from features.intel import build, write
    from features.intel.schema import validate

    db.init_db()
    _seed(db)
    doc = build(days=7)
    assert validate(doc) == []
    assert doc["schema_version"] == 1 and doc["window_days"] == 7
    assert any(b["workflow"] == "CRM Sync" for b in doc["bands"])
    assert any(d["axis"] == "workflow" and d["label"] == "CRM Sync" for d in doc["demand"])
    assert doc["watch_suggestions"] and "expression" in doc["watch_suggestions"][0]
    assert doc["funnel_summary"]["insufficient"] is True
    blob = json.dumps(doc)
    for forbidden in ("CRM-1", "hubspot crm sync", "job_id", "title"):
        assert forbidden not in blob

    path = write(doc, str(tmp_path / "intel" / "market_intel_latest.json"))
    on_disk = json.loads(pathlib.Path(path).read_text())
    assert on_disk["bands"] == doc["bands"]
    assert not (tmp_path / "intel" / "market_intel_latest.json.tmp").exists()


def test_schema_validator_catches_problems():
    from features.intel.schema import validate

    assert validate("nope") == ["document must be an object"]
    bad = {"schema_version": 1, "generated_at": "", "data_as_of": "", "window_days": 7,
           "scoring_version": "v", "bands": [{"workflow": "x"}], "demand": [], "watch_suggestions": [],
           "funnel_summary": {"days": 7, "stages": {}, "win_rate_shrunk": 0.1, "insufficient": True,
                              "job_id": "leak"}}  # fmt: skip
    problems = validate(bad)
    assert any("bands[0]: missing n" in p for p in problems)
    assert any("forbidden key job_id" in p for p in problems)
