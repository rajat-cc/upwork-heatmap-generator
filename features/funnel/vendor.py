"""Sync your own submitted proposals from the API into the ledger (gated).

`vendorProposals` lists the freelancer's proposals read-only. Whether this key
may call it is answered by `docs/api_probe.json` (`capabilities.vendor_proposals`);
until a live probe has said yes, the step is skipped with a reason.

Status → event mapping is deliberately conservative: every proposal becomes a
SUBMITTED event at its creation time; only statuses listed below also produce
HIRED or LOST. Adjust the sets once the probe has printed the real enum.
"""

from __future__ import annotations

import json
from pathlib import Path

from config import ROOT_DIR
from core.logging_setup import get_logger
from db import insert_events
from features.funnel.ingest import event_id
from fetcher import Transport, gql

log = get_logger(__name__)

PROBE_PATH = Path(ROOT_DIR) / "docs" / "api_probe.json"

HIRED_STATUSES = {"HIRED", "ACTIVE_CONTRACT", "OFFER_ACCEPTED"}
LOST_STATUSES = {"ARCHIVED", "DECLINED", "WITHDRAWN", "REJECTED", "CLOSED", "EXPIRED"}

VENDOR_PROPOSALS_QUERY = """
query MyProposals($after: String) {
  vendorProposals(
    sortAttribute: { field: CREATED_ON, order: DESC }
    pagination: { first: 50, after: $after }
  ) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        createdDateTime
        status { name }
        proposedTerms {
          ... on ProposalTerms {
            hourlyRate { rawValue }
            fixedPriceAmount { rawValue }
          }
        }
        jobPosting { id ciphertext title }
      }
    }
  }
}
"""


def capability(probe_path: str | Path | None = None) -> tuple[bool, str]:
    """(enabled, reason) from the live probe report."""
    p = Path(probe_path or PROBE_PATH)
    if not p.is_file():
        return False, "no docs/api_probe.json yet — run `make probe`"
    try:
        doc = json.loads(p.read_text())
    except ValueError:
        return False, "docs/api_probe.json is not valid JSON"
    if doc.get("offline"):
        return False, "docs/api_probe.json came from the offline fixture — run `make probe`"
    if doc.get("capabilities", {}).get("vendor_proposals"):
        return True, "probe: vendorProposals readable"
    err = (doc.get("checks", {}).get("vendor_proposals") or {}).get("error") or "not readable"
    return False, f"probe: vendorProposals {err}"


def fetch_vendor_proposals(
    *, transport: Transport | None = None, token: str | None = None, max_pages: int = 20
) -> list[dict]:
    nodes: list[dict] = []
    after: str | None = None
    for _ in range(max_pages):
        data = gql(VENDOR_PROPOSALS_QUERY, {"after": after}, transport=transport, token=token)
        conn = data.get("vendorProposals") or {}
        for edge in conn.get("edges") or []:
            node = (edge or {}).get("node")
            if node:
                nodes.append(node)
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = info["endCursor"]
    return nodes


def _iso(value: str | None) -> str:
    return (value or "").replace("+0000", "Z").replace("+00:00", "Z")


def events_from_proposals(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        job = n.get("jobPosting") or {}
        job_id = str(job.get("id") or "")
        ts = _iso(n.get("createdDateTime"))
        if not job_id or not ts:
            continue
        status = ((n.get("status") or {}).get("name") or "").upper()
        terms = n.get("proposedTerms") or {}
        hourly = (terms.get("hourlyRate") or {}).get("rawValue")
        fixed = (terms.get("fixedPriceAmount") or {}).get("rawValue")
        bid_amount = float(hourly or fixed) if (hourly or fixed) else None
        bid_type = "hourly" if hourly else "fixed" if fixed else None
        meta = {
            "status": status,
            "proposal_id": n.get("id"),
            "ciphertext": job.get("ciphertext") or "",
        }
        out.append(
            {
                "event_id": event_id("api", job_id, "SUBMITTED", ts),
                "job_id": job_id,
                "event": "SUBMITTED",
                "ts": ts,
                "source": "api",
                "bid_amount": bid_amount,
                "bid_type": bid_type,
                "meta": meta,
            }
        )
        follow = (
            "HIRED" if status in HIRED_STATUSES else "LOST" if status in LOST_STATUSES else None
        )
        if follow:
            out.append(
                {
                    "event_id": event_id("api", job_id, follow, ts),
                    "job_id": job_id,
                    "event": follow,
                    "ts": ts,
                    "source": "api",
                    "meta": meta,
                }
            )
    return out


def sync_vendor_proposals(
    *,
    transport: Transport | None = None,
    token: str | None = None,
    probe_path=None,
    force: bool = False,
) -> dict:
    enabled, reason = capability(probe_path)
    if not enabled and not force:
        return {"skipped": reason, "seen": 0, "inserted": 0}
    nodes = fetch_vendor_proposals(transport=transport, token=token)
    events = events_from_proposals(nodes)
    return {
        "skipped": None,
        "seen": len(events),
        "inserted": insert_events(events),
        "proposals": len(nodes),
    }
