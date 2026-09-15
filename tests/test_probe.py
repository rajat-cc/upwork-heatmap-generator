"""The capability probe runs end-to-end against the offline fixture."""

from __future__ import annotations

import json


def test_offline_probe_writes_report(tmp_path, monkeypatch):
    import fetcher
    from core.ratelimit import TokenBucket

    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    from core.probe import run_probe

    out = tmp_path / "probe.json"
    report = run_probe(offline=True, out=out, quiet=True)

    assert out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk["capabilities"] == report["capabilities"]
    assert report["offline"] is True
    assert report["org_id"] == "offline-org"

    caps = report["capabilities"]
    # The fixture models a key where the detail endpoint is scope-gated but the
    # contents query works, and the client object carries no identity field.
    assert caps["detail_query"] is False
    assert report["checks"]["detail_query"]["permission_error"] is True
    assert caps["contents_query"] is True
    assert caps["contents_has_activity"] is False
    assert caps["client_identity_fields"] == []
    assert "totalPostedJobs" in caps["client_fields"]
    assert "categoryIds_any" in caps["filter_fields"]
    assert caps["vendor_proposals"] is True
    assert "ACTIVE" in caps["proposal_statuses"]


def test_probe_records_transport_failures_without_crashing(tmp_path, monkeypatch):
    import fetcher
    from core.ratelimit import TokenBucket

    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    from core.probe import load_offline_fixture, run_probe

    # Drop two fixtures: the missing keys surface as recorded errors, not exceptions.
    responses = load_offline_fixture()
    del responses["ProbeProposals"]
    del responses["IntrospectType:VendorProposalStatusName"]
    transport = fetcher.FixtureTransport(responses)

    report = run_probe(offline=True, out=tmp_path / "p.json", transport=transport, quiet=True)
    assert report["checks"]["vendor_proposals"]["ok"] is False
    assert report["capabilities"]["vendor_proposals"] is False
    assert report["capabilities"]["proposal_statuses"] == []
