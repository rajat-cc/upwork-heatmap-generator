"""vendorProposals sync: gated on the live probe, conservative status mapping, idempotent."""

from __future__ import annotations

import json

from core.ratelimit import TokenBucket

_PAGE = {
    "MyProposals": {
        "data": {
            "vendorProposals": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {"node": {"id": "p1", "createdDateTime": "2026-09-01T10:00:00+0000",
                              "status": {"name": "ACTIVE"},
                              "proposedTerms": {"hourlyRate": {"rawValue": "45"}, "fixedPriceAmount": None},
                              "jobPosting": {"id": "700000000000000001", "ciphertext": "~01abc", "title": "t"}}},
                    {"node": {"id": "p2", "createdDateTime": "2026-08-20T10:00:00+0000",
                              "status": {"name": "ARCHIVED"},
                              "proposedTerms": {"hourlyRate": None, "fixedPriceAmount": {"rawValue": "800"}},
                              "jobPosting": {"id": "700000000000000002", "ciphertext": "~01def", "title": "t2"}}},
                    {"node": {"id": "p3", "createdDateTime": "2026-08-25T10:00:00+0000",
                              "status": {"name": "HIRED"},
                              "proposedTerms": {"hourlyRate": {"rawValue": "60"}, "fixedPriceAmount": None},
                              "jobPosting": {"id": "700000000000000003", "ciphertext": "~01ghi", "title": "t3"}}},
                    {"node": {"id": "p4", "createdDateTime": None, "status": {"name": "ACTIVE"},
                              "proposedTerms": None, "jobPosting": None}},
                ],
            }
        }
    }
}  # fmt: skip


def test_capability_gate(tmp_path):
    from features.funnel.vendor import capability

    assert capability(tmp_path / "missing.json")[0] is False
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"offline": True, "capabilities": {"vendor_proposals": True}}))
    assert capability(off)[0] is False
    no = tmp_path / "no.json"
    no.write_text(json.dumps({"offline": False, "capabilities": {"vendor_proposals": False},
                              "checks": {"vendor_proposals": {"error": "Your OAuth2 permissions ..."}}}))  # fmt: skip
    enabled, reason = capability(no)
    assert enabled is False and "OAuth2" in reason
    yes = tmp_path / "yes.json"
    yes.write_text(json.dumps({"offline": False, "capabilities": {"vendor_proposals": True}}))
    assert capability(yes) == (True, "probe: vendorProposals readable")


def test_mapping_and_idempotent_sync(isolated_db, tmp_path, monkeypatch):
    import db
    import fetcher
    from features.funnel.vendor import events_from_proposals, sync_vendor_proposals

    db.init_db()
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    nodes = _PAGE["MyProposals"]["data"]["vendorProposals"]["edges"]
    events = events_from_proposals([e["node"] for e in nodes])
    kinds = [(e["job_id"], e["event"]) for e in events]
    assert kinds == [
        ("700000000000000001", "SUBMITTED"),
        ("700000000000000002", "SUBMITTED"), ("700000000000000002", "LOST"),
        ("700000000000000003", "SUBMITTED"), ("700000000000000003", "HIRED"),
    ]  # fmt: skip
    assert events[0]["bid_amount"] == 45.0 and events[0]["bid_type"] == "hourly"
    assert events[1]["bid_amount"] == 800.0 and events[1]["bid_type"] == "fixed"
    assert events[0]["ts"] == "2026-09-01T10:00:00Z"

    probe = tmp_path / "probe.json"
    transport = fetcher.FixtureTransport(_PAGE)
    skipped = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert skipped["skipped"] and skipped["inserted"] == 0

    probe.write_text(json.dumps({"offline": False, "capabilities": {"vendor_proposals": True}}))
    first = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert first["skipped"] is None and first["inserted"] == 5 and first["proposals"] == 4
    second = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert second["inserted"] == 0
    assert [r["event"] for r in db.events_for_job("700000000000000003")] == ["SUBMITTED", "HIRED"]
