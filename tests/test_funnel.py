"""Funnel maths on a known ledger: distinct-job stages, shrunk win rate, cost per hire, segments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _ts(days_ago: int, hour: int = 14) -> str:
    return (
        (datetime.now(UTC) - timedelta(days=days_ago))
        .replace(hour=hour, minute=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _ev(job, event, ts, **kw):
    return {
        "event_id": f"{job}-{event}-{ts}",
        "job_id": job,
        "event": event,
        "ts": ts,
        "source": "manual",
        **kw,
    }


def _seed(db):
    meta_us = {"category": "web_mobile_software_dev", "verified": True, "client_spend": 20000,
               "client_hires": 8, "budget_type": "HOURLY", "budget_mid": 60, "experience": "Expert",
               "labels": {"platform": ["n8n"], "workflow": ["CRM Sync"]}}  # fmt: skip
    meta_new = {"category": "sales_marketing", "verified": True, "client_spend": 0, "client_hires": 0,
                "budget_type": "FIXED", "budget_mid": 600, "experience": "", "labels": {}}  # fmt: skip
    events = []
    # Job A: full path to hired, notified twice (agent duplicate)
    events += [_ev("A", "NOTIFIED", _ts(10, 9), meta=meta_us), _ev("A", "NOTIFIED", _ts(10, 10), meta=meta_us),
               _ev("A", "DRAFTED", _ts(10)), _ev("A", "ACCEPTED", _ts(10)),
               _ev("A", "SUBMITTED", _ts(9), bid_amount=55, bid_type="hourly", connects_spent=16),
               _ev("A", "VIEWED", _ts(8)), _ev("A", "INTERVIEW", _ts(7)), _ev("A", "HIRED", _ts(5))]  # fmt: skip
    # Job B: submitted, lost; connects unknown → default applied
    events += [_ev("B", "NOTIFIED", _ts(6, 9), meta=meta_new), _ev("B", "DRAFTED", _ts(6)),
               _ev("B", "SUBMITTED", _ts(6), bid_amount=500, bid_type="fixed"), _ev("B", "LOST", _ts(2))]  # fmt: skip
    # Job C: notified only; Job D: filtered
    events += [
        _ev("C", "NOTIFIED", _ts(3, 22), meta=meta_new),
        _ev("D", "FILTERED", _ts(3), meta=meta_new),
    ]
    # Job E: outside the window
    events += [_ev("E", "NOTIFIED", _ts(60), meta=meta_us)]
    assert db.insert_events(events) == len(events)


def test_funnel_report(isolated_db, monkeypatch):
    import config
    import db
    from features.funnel.analyzer import analyze

    db.init_db()
    _seed(db)
    monkeypatch.setattr(config, "CONNECT_PRICE_USD", 0.15)
    monkeypatch.setattr(config, "DEFAULT_CONNECTS_PER_PROPOSAL", 12)

    r = analyze(days=30, tz_name="UTC")
    assert r.jobs == 4  # E is outside the window
    assert r.stages == {"NOTIFIED": 3, "DRAFTED": 2, "ACCEPTED": 1, "SUBMITTED": 2,
                        "VIEWED": 1, "INTERVIEW": 1, "HIRED": 1}  # fmt: skip
    assert r.side_exits == {"FILTERED": 1, "LOST": 1}
    conv = {(a, b): rate for a, b, rate in r.conversions}
    assert conv[("NOTIFIED", "DRAFTED")] == 2 / 3
    assert conv[("ACCEPTED", "SUBMITTED")] == 2.0  # B was submitted without an Accept event

    assert r.win.submitted == 2 and r.win.hired == 1
    assert r.win.raw == 0.5
    assert abs(r.win.shrunk - (1 + 1) / (2 + 10)) < 1e-9  # Beta(1, 9) shrinkage
    assert r.win.insufficient is True

    assert r.connects_spent == 16 + 12 and r.connects_assumed == 1
    assert r.cost_per_hire_usd == round(28 * 0.15 / 1, 2)
    assert r.med_winning_bid == 55.0 and r.med_submitted_bid == 277.5

    seg = {row.value: row for row in r.segments["client_segment"]}
    assert seg["champion"].notified == 1 and seg["champion"].hired == 1
    assert seg["new"].notified == 2 and seg["new"].submitted == 1 and seg["new"].hired == 0
    bands = {row.value: row for row in r.segments["budget_band"]}
    assert bands["hourly $50–100"].hired == 1 and bands["fixed $500–2k"].submitted == 1
    hours = {row.value: row for row in r.segments["hour"]}
    assert hours["09:00"].notified == 2  # first NOTIFIED timestamp decides the hour
    platforms = {row.value: row for row in r.segments["platform"]}
    assert platforms["n8n"].hired == 1 and platforms["untagged"].notified == 2


def test_empty_window_reports_nothing(isolated_db):
    import db
    from features.funnel.analyzer import analyze

    db.init_db()
    r = analyze(days=7)
    assert r.jobs == 0 and r.events == 0 and r.win.insufficient
    assert all(v == 0 for v in r.stages.values())


def test_client_segment_is_unknown_without_client_fields():
    from features.funnel.analyzer import client_segment

    assert client_segment({"status": "ACCEPTED", "proposal_id": "p1"}) == "unknown"
    assert client_segment({"verified": 0, "client_spend": 0, "client_hires": 0}) == "risky"
    assert client_segment({"verified": 1, "client_spend": 0, "client_hires": 0}) == "new"
