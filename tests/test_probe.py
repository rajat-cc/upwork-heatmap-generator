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


def test_probe_retries_a_transient_graphql_error_once(tmp_path, monkeypatch):
    import fetcher
    from core.ratelimit import TokenBucket

    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    from core.probe import load_offline_fixture, run_probe

    inner = fetcher.FixtureTransport(load_offline_fixture())
    failed: list[str] = []

    def flaky(body, headers):
        # Upwork's "Transform timeout" arrives as a 200 with errors[] and no data.
        if "ProbeContents" in body["query"] and not failed:
            failed.append("once")
            return fetcher.Response(
                200,
                {
                    "errors": [{"message": "Transform timeout (1s) - path: /generic/x"}],
                    "data": None,
                },
            )
        return inner(body, headers)

    naps: list[float] = []
    report = run_probe(
        offline=True, out=tmp_path / "p.json", transport=flaky, quiet=True, sleep=naps.append
    )
    assert report["capabilities"]["contents_query"] is True
    assert report["checks"]["contents_query"]["retried"] == 1
    assert naps == [2.0]
    # Permission errors are answers, not glitches: the gated detail query is not retried.
    assert report["checks"]["detail_query"]["retried"] == 0


def test_probe_reads_detail_activity_and_client_id_when_detail_is_reachable(tmp_path, monkeypatch):
    import fetcher
    from core import capabilities
    from core.ratelimit import TokenBucket

    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    from core.probe import load_offline_fixture, run_probe

    responses = load_offline_fixture()
    responses["ProbeDetail"] = {"data": {"marketplaceJobPosting": {"id": "9000000000000000001"}}}
    out = tmp_path / "p.json"
    report = run_probe(
        offline=True, out=out, transport=fetcher.FixtureTransport(responses), quiet=True
    )
    caps = report["capabilities"]
    assert caps["detail_query"] is True
    assert caps["detail_has_activity"] is True and "totalHired" in caps["detail_activity_fields"]
    assert caps["detail_client_identity"] is True
    assert "activityStat" in caps["detail_fields"]

    # The offline flag keeps the report from counting as a live answer …
    assert capabilities.detail_snapshots_reason(out) == "gated: run `make probe` first"
    # … but the same shape from a live run enables detail-stage snapshots.
    live = dict(report, offline=False)
    out.write_text(json.dumps(live))
    assert capabilities.detail_snapshots_reason(out) is None
    assert capabilities.detail_has_activity(out) and capabilities.detail_client_identity(out)
    gated = dict(live)
    gated["capabilities"] = dict(caps, detail_has_activity=False)
    out.write_text(json.dumps(gated))
    assert "no activity fields" in capabilities.detail_snapshots_reason(out)
