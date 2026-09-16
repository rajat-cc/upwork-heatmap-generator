"""vendorProposals sync: gated on the live probe, status mapping, dedup across buckets, incremental."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from core.ratelimit import TokenBucket


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_ORG = json.loads(
    (Path(__file__).parent / "fixtures" / "graphql" / "sync_offline.json").read_text()
)["OrgId"]

# Upwork's shape: DateTime objects with epoch-ms rawValue, status on the node,
# the job under marketplaceJobPosting. The same page is served to every bucket.
_NODES = [
    {"id": "p1", "status": {"status": "Accepted"},
     "auditDetails": {"createdDateTime": {"rawValue": "1787306400000"},
                      "modifiedDateTime": {"rawValue": "1787310000000"}},
     "terms": {"chargeRate": {"rawValue": "45.0", "currency": "USD"}},
     "viewedByClient": False,
     "marketplaceJobPosting": {"id": "700000000000000001", "contractTerms": {"contractType": "HOURLY"}}},
    {"id": "p2", "status": {"status": "Archived"},
     "auditDetails": {"createdDateTime": {"rawValue": "1785578400000"},
                      "modifiedDateTime": {"rawValue": "1786010400000"}},
     "terms": {"chargeRate": {"rawValue": "800.0", "currency": "USD"}},
     "viewedByClient": True,
     "marketplaceJobPosting": {"id": "700000000000000002", "contractTerms": {"contractType": "FIXED"}}},
    {"id": "p3", "status": {"status": "Activated"},
     "auditDetails": {"createdDateTime": {"rawValue": "1786010400000"},
                      "modifiedDateTime": {"rawValue": "1786096800000"}},
     "terms": {"chargeRate": {"rawValue": "60.0", "currency": "USD"}},
     "viewedByClient": False,
     "marketplaceJobPosting": {"id": "700000000000000003", "contractTerms": {"contractType": "HOURLY"}}},
    {"id": "p4", "status": {"status": "Accepted"}, "auditDetails": None, "terms": None,
     "viewedByClient": False, "marketplaceJobPosting": None},                # unusable → skipped
]  # fmt: skip

_PAGE = {
    "OrgId": _ORG,
    "MyProposals": {
        "data": {
            "vendorProposals": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [{"node": n} for n in _NODES],
            }
        }
    },
}


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


def test_mapping_dedup_and_idempotent_sync(isolated_db, tmp_path, monkeypatch):
    import db
    import fetcher
    from features.funnel import vendor
    from features.funnel.vendor import events_from_proposals, sync_vendor_proposals

    db.init_db()
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))

    events = events_from_proposals(_NODES)
    kinds = [(e["job_id"], e["event"]) for e in events]
    assert kinds == [
        ("700000000000000001", "SUBMITTED"),
        ("700000000000000002", "SUBMITTED"), ("700000000000000002", "VIEWED"), ("700000000000000002", "LOST"),
        ("700000000000000003", "SUBMITTED"), ("700000000000000003", "HIRED"),
    ]  # fmt: skip
    assert events[0]["bid_amount"] == 45.0 and events[0]["bid_type"] == "hourly"
    assert events[1]["bid_amount"] == 800.0 and events[1]["bid_type"] == "fixed"
    assert events[0]["ts"] == _ts(1787306400000)  # SUBMITTED at creation
    assert events[5]["ts"] == _ts(1786096800000)  # HIRED at the modification time
    assert (
        events[0]["meta"]["status"] == "ACCEPTED"
        and events[0]["meta"]["modified_ms"] == 1787310000000
    )
    # ids depend on the proposal and event only, never on timestamps
    assert events[0]["event_id"] == vendor._event_id("p1", "SUBMITTED")

    probe = tmp_path / "probe.json"
    transport = fetcher.FixtureTransport(_PAGE)
    skipped = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert skipped["skipped"] and skipped["inserted"] == 0

    probe.write_text(json.dumps({"offline": False, "capabilities": {"vendor_proposals": True}}))
    first = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    # Every bucket served the same page; proposals are deduplicated across them.
    assert first["skipped"] is None and first["proposals"] == 4 and first["inserted"] == 6
    assert first["incremental"] is False and first["complete"] is True
    pages = [c for c in transport.calls if "MyProposals" in c["query"]]
    assert len(pages) == len(vendor.BUCKETS)

    second = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert second["inserted"] == 0 and second["incremental"] is True
    assert [r["event"] for r in db.events_for_job("700000000000000003")] == ["SUBMITTED", "HIRED"]
    assert vendor.high_water_mark() == 1787310000000


def test_incremental_paging_stops_at_the_high_water_mark(monkeypatch):
    import fetcher
    from features.funnel.vendor import fetch_vendor_proposals

    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))

    def node(pid: str, modified_ms: int) -> dict:
        return {"id": pid, "status": {"status": "Accepted"},
                "auditDetails": {"createdDateTime": {"rawValue": str(modified_ms - 1000)},
                                 "modifiedDateTime": {"rawValue": str(modified_ms)}},
                "terms": {"chargeRate": {"rawValue": "1", "currency": "USD"}},
                "marketplaceJobPosting": {"id": "1", "contractTerms": {"contractType": "HOURLY"}}}  # fmt: skip

    # Two pages per bucket, newest modification first; page 2 is older than the mark.
    pages = {
        None: {"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
               "edges": [{"node": node("new", 2_000)}, {"node": node("mid", 1_500)}]},
        "c1": {"pageInfo": {"hasNextPage": False, "endCursor": None},
               "edges": [{"node": node("old", 500)}]},
    }  # fmt: skip
    calls: list[dict] = []

    def transport(body, headers):
        calls.append(body)
        if "OrgId" in body["query"]:
            return fetcher.Response(200, _ORG)
        after = (body.get("variables") or {}).get("after")
        return fetcher.Response(200, {"data": {"vendorProposals": pages[after]}})

    everything, complete = fetch_vendor_proposals(
        transport=transport, token="t", buckets=("Archived",)
    )
    assert sorted(n["id"] for n in everything) == ["mid", "new", "old"] and complete
    calls.clear()
    recent, _ = fetch_vendor_proposals(
        transport=transport, token="t", buckets=("Archived",), since_ms=1_600
    )
    # Page 1 still holds something newer than the mark, page 2 does not → 2 calls, then stop.
    assert sorted(n["id"] for n in recent) == ["mid", "new", "old"]
    assert len([c for c in calls if "MyProposals" in c["query"]]) == 2
    calls.clear()
    nothing_new, _ = fetch_vendor_proposals(
        transport=transport, token="t", buckets=("Archived",), since_ms=2_000
    )
    assert len([c for c in calls if "MyProposals" in c["query"]]) == 1
    assert sorted(n["id"] for n in nothing_new) == ["mid", "new"]


def test_transient_timeouts_are_retried_and_a_failing_bucket_keeps_the_mark(
    isolated_db, tmp_path, monkeypatch
):
    import db
    import fetcher
    from features.funnel import vendor
    from features.funnel.vendor import sync_vendor_proposals

    db.init_db()
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    monkeypatch.setattr(vendor, "BUCKETS", ("Archived", "Declined"))
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({"offline": False, "capabilities": {"vendor_proposals": True}}))
    timeout = {
        "errors": [{"message": "Transform timeout (1s) - path: /auth/vendors/1/proposals"}],
        "data": None,
    }
    attempts: dict[str, int] = {"Archived": 0, "Declined": 0}
    broken = {"Declined"}

    def transport(body, headers):
        if "OrgId" in body["query"]:
            return fetcher.Response(200, _ORG)
        bucket = body["variables"]["status"]
        attempts[bucket] += 1
        if bucket in broken:
            return fetcher.Response(200, timeout)  # keeps failing
        if bucket == "Archived" and attempts[bucket] == 1:
            return fetcher.Response(200, timeout)  # recovers on the retry
        return fetcher.Response(200, _PAGE["MyProposals"])

    naps: list[float] = []
    result = sync_vendor_proposals(
        transport=transport, token="t", probe_path=probe, sleep=naps.append
    )
    assert result["complete"] is False and result["proposals"] == 4 and result["inserted"] == 6
    assert attempts == {"Archived": 2, "Declined": vendor.TRANSIENT_RETRIES}
    assert naps == [2.0, 2.0, 4.0]  # one retry nap for Archived, two for Declined
    assert vendor.high_water_mark() is None  # an incomplete crawl never advances the mark

    # Once every bucket answers, the mark is set and the next run is incremental.
    broken.clear()
    good = sync_vendor_proposals(transport=transport, token="t", probe_path=probe)
    assert good["complete"] is True and good["inserted"] == 0
    assert vendor.high_water_mark() == 1787310000000
    assert sync_vendor_proposals(transport=transport, token="t", probe_path=probe)["incremental"]
