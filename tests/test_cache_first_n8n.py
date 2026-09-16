"""A job whose text was purged is still analysed from its cached labels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_purged_job_survives_in_the_n8n_lens(isolated_db):
    import db
    from features.n8n import analyzer, classifier

    db.init_db()
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    db.upsert_jobs(
        [
            {"id": "p", "title": "n8n voice agent for a dental clinic", "published_at": today,
             "description": "vapi calls, google calendar booking", "skills": '["n8n"]',
             "budget_type": "HOURLY", "budget_min": 40, "budget_max": 60, "budget_amount": 50,
             "total_applicants": 4, "client_verified": 1},
            {"id": "live", "title": "n8n HubSpot sync", "published_at": today,
             "description": "crm sync", "budget_type": "FIXED", "budget_amount": 300},
        ]
    )  # fmt: skip
    classifier.persist(classifier.classify_rows(db.get_jobs_by_ids(["p", "live"])))

    stamp = (datetime.now(UTC) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_conn() as conn:
        conn.execute("UPDATE jobs SET last_fetched_at = ? WHERE id = 'p'", (stamp,))
    assert db.purge_text(24) == 1

    jobs = analyzer.load_n8n_jobs(days=7)
    assert sorted(j.id for j in jobs) == ["live", "p"]
    purged = next(j for j in jobs if j.id == "p")
    assert purged.is_purged and purged.display_title == "[purged] p"

    report = analyzer.analyze(jobs)
    assert report.total_jobs == 2
    assert "Healthcare" in purged.industries  # from the cache, text is gone
    assert "Voice / Telephony" in purged.workflows
    assert "n8n" in purged.platforms
    assert purged.opp_score > 0
    live = next(j for j in jobs if j.id == "live")
    assert "CRM Sync" in live.workflows  # live regex path still works
    stats = report.workflow_stats["Voice / Telephony"]
    assert stats.med_hourly == 50.0 and stats.n_hourly == 1
