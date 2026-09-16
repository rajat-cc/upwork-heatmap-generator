"""The whole sync path runs offline against the recorded page."""

from __future__ import annotations

import json


def test_sync_offline_end_to_end(isolated_db, tmp_path, monkeypatch):
    import config
    import db
    import fetcher
    from core.ratelimit import TokenBucket

    monkeypatch.setattr(config, "EXPORTS_DIR", str(tmp_path / "exports"))
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    from features.sync import api as sync_api

    result = sync_api.run(offline=True, quiet=True)

    assert result.ok and result.exit_code() == 0
    assert result.watches_source == "offline fixture"
    assert [(w.label, w.jobs, w.status) for w in result.watches] == [("offline-n8n", 3, "done")]
    assert result.classified_jobs == 3
    assert result.classification_rows > 0
    assert result.purged == 0
    assert result.jobs_total == 3
    assert result.last_success_at

    with db.get_conn() as conn:
        runs = conn.execute("SELECT search_term, status, jobs_seen FROM fetch_runs").fetchall()
        labels = {
            (r["job_id"], r["axis"], r["label"])
            for r in conn.execute("SELECT job_id, axis, label FROM job_classifications")
        }
        countries = dict(conn.execute("SELECT id, client_country FROM jobs").fetchall())
        rates = dict(conn.execute("SELECT id, hire_rate FROM jobs").fetchall())
        tiers = dict(conn.execute("SELECT id, contractor_tier FROM jobs").fetchall())
    assert [tuple(r) for r in runs] == [("offline-n8n", "done", 3)]
    assert ("9000000000000000001", "platform", "n8n") in labels
    assert ("9000000000000000001", "industry", "Healthcare") in labels
    assert ("9000000000000000002", "platform", "Make") in labels
    assert countries == {
        "9000000000000000001": "US",
        "9000000000000000002": "GB",
        "9000000000000000003": "IN",
    }
    assert rates["9000000000000000001"] == 0.6
    assert tiers["9000000000000000002"] == "UNKNOWN"  # nulled by the API, stored honestly

    status = json.loads((tmp_path / "exports" / "status.json").read_text())
    assert status["ok"] is True
    assert status["watches"][0]["jobs"] == 3
    assert status["last_success_at"] == result.last_success_at

    # A second run re-observes the same jobs without duplicating anything.
    again = sync_api.run(offline=True, quiet=True)
    assert again.ok and again.jobs_total == 3
    with db.get_conn() as conn:
        fetch_counts = {r[0] for r in conn.execute("SELECT fetch_count FROM jobs")}
    assert fetch_counts == {2}


def test_sync_reports_reauth_and_exits_2(isolated_db, tmp_path, monkeypatch):
    import config
    import fetcher

    monkeypatch.setattr(config, "EXPORTS_DIR", str(tmp_path / "exports"))
    from features.sync import api as sync_api

    def _no_token():
        raise RuntimeError("No valid token found. Run:  make auth")

    monkeypatch.setattr(fetcher.auth, "get_access_token", _no_token)
    monkeypatch.setattr(sync_api, "load_watches", lambda: ([sync_api.Watch("w", "n8n")], "test"))

    result = sync_api.run(offline=False, quiet=True)
    assert result.error == "reauth"
    assert result.exit_code() == 2
    assert result.watches[0].status == "error"
    status = json.loads((tmp_path / "exports" / "status.json").read_text())
    assert status["error"] == "reauth" and status["ok"] is False
