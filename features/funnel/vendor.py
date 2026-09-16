"""Sync your own proposals from the API into the ledger (gated on the live probe).

`vendorProposals` requires a status filter and a sort attribute, accepts at
most 20 per page, and its filter buckets do not partition the history:
"Declined", "Withdrawn" and "Hired" all return the same open-proposals list
(~1,100 rows on this account) while "Archived" holds the closed history and
"Accepted"/"Activated" the few live offers and contracts. So every bucket is
paged, proposals are deduplicated by id, and the node's own `status.status`
decides the outcome:

  * every proposal      → SUBMITTED at its creation time (bid = chargeRate)
  * Activated, Hired    → HIRED   (a contract exists)
  * Declined, Withdrawn, Archived → LOST
  * viewedByClient      → VIEWED
  * Accepted, Pending, Offered → no follow-up event ("Accepted" is Upwork's
    word for a submitted, still-open proposal)

Pages are sorted by modification time, newest first, and a bucket stops paging
once it reaches proposals not modified since the last complete crawl (the
mark lives in `sync_state`), so a routine run costs one call per bucket.
Upwork's transient "Transform timeout" is retried per page; a bucket that
keeps failing is skipped, whatever was fetched is still written, and the mark
does not advance until a crawl completes. Event ids derive from the proposal
id, never from a timestamp, so a later status change adds a HIRED or LOST row
without ever duplicating the SUBMITTED one. `connectsBid` and
`inviteToInterview` need a scope this key lacks and are not requested.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from config import ROOT_DIR
from core.logging_setup import get_logger
from db import get_state, insert_events, set_state
from fetcher import GraphQLError, TransientError, Transport, get_org_id, gql

log = get_logger(__name__)

PROBE_PATH = Path(ROOT_DIR) / "docs" / "api_probe.json"

# Filter buckets to page, most informative first (see the module docstring).
BUCKETS = (
    "Archived",
    "Declined",
    "Accepted",
    "Activated",
    "Hired",
    "Withdrawn",
    "Offered",
    "Pending",
)
PAGE_SIZE = 20  # the API rejects larger pages
DEFAULT_MAX_PAGES = 200  # per bucket, first (full) crawl
TRANSIENT_RETRIES = 3
STATE_KEY = "vendor_proposals.modified_hwm_ms"  # newest modification seen by a complete crawl

HIRED_STATUSES = {"ACTIVATED", "HIRED"}
LOST_STATUSES = {"DECLINED", "WITHDRAWN", "ARCHIVED"}

VENDOR_PROPOSALS_QUERY = """
query MyProposals($status: VendorProposalStatusFilterInput!, $after: String) {
  vendorProposals(
    filter: {status_eq: $status}
    sortAttribute: {field: MODIFIEDDATETIME, sortOrder: DESC}
    pagination: {first: 20, after: $after}
  ) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        status { status }
        auditDetails { createdDateTime { rawValue } modifiedDateTime { rawValue } }
        terms { chargeRate { rawValue currency } }
        viewedByClient
        marketplaceJobPosting { id contractTerms { contractType } }
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


# ─── Time helpers ───────────────────────────────────────────────────────────


def _raw_ms(value) -> int | None:
    """Upwork's `DateTime.rawValue` is epoch milliseconds as a string."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("rawValue")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _iso(value) -> str:
    """Epoch-ms (or an ISO string, in fixtures) → `YYYY-MM-DDTHH:MM:SSZ`."""
    ms = _raw_ms(value)
    if ms is not None:
        return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        value = value.get("rawValue")
    text = str(value or "")
    return text.replace("+0000", "Z").replace("+00:00", "Z")


def _event_id(proposal_id: str, event: str) -> str:
    """Stable per proposal and event, so status changes never duplicate rows."""
    return hashlib.sha1(f"api|proposal:{proposal_id}|{event}".encode()).hexdigest()[:24]


# ─── Fetch ──────────────────────────────────────────────────────────────────


def _is_transient(exc: GraphQLError) -> bool:
    return (
        not exc.is_permission_error
        and not exc.is_validation_error
        and "timeout" in str(exc).lower()
    )


def fetch_vendor_proposals(
    *,
    transport: Transport | None = None,
    token: str | None = None,
    since_ms: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    buckets: tuple[str, ...] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict], bool]:
    """(proposals across the filter buckets deduplicated by id, crawl complete?).

    With `since_ms`, a bucket stops paging once a page holds nothing modified
    after that instant (pages are newest-modified first). A page that keeps
    timing out ends its bucket and marks the crawl incomplete; permission and
    validation errors propagate, since retrying cannot change them.
    """
    org_id = get_org_id(transport=transport, token=token)
    seen: dict[str, dict] = {}
    complete = True
    for bucket in buckets or BUCKETS:
        after: str | None = None
        for _ in range(max_pages):
            data = None
            for attempt in range(1, TRANSIENT_RETRIES + 1):
                try:
                    data = gql(
                        VENDOR_PROPOSALS_QUERY,
                        {"status": bucket, "after": after},
                        transport=transport,
                        org_id=org_id,
                        token=token,
                    )
                    break
                except GraphQLError as exc:
                    if not _is_transient(exc):
                        raise
                    log.warning("vendorProposals %s page: %s (attempt %d)", bucket, exc, attempt)
                    if attempt < TRANSIENT_RETRIES:
                        sleep(2.0 * attempt)
                except TransientError as exc:
                    log.warning("vendorProposals %s page: %s", bucket, exc)
                    break
            if data is None:
                complete = False
                break  # give up on this bucket; the mark will not advance
            conn = data.get("vendorProposals") or {}
            edges = conn.get("edges") or []
            page_newest: int | None = None
            for edge in edges:
                node = (edge or {}).get("node")
                pid = str((node or {}).get("id") or "")
                if not pid:
                    continue
                modified = _raw_ms((node.get("auditDetails") or {}).get("modifiedDateTime"))
                if modified is not None:
                    page_newest = modified if page_newest is None else max(page_newest, modified)
                seen.setdefault(pid, node)
            info = conn.get("pageInfo") or {}
            if not edges or not info.get("hasNextPage") or not info.get("endCursor"):
                break
            if since_ms is not None and (page_newest is None or page_newest <= since_ms):
                break  # everything from here on was already synced
            after = info["endCursor"]
    return list(seen.values()), complete


# ─── Map to ledger events ───────────────────────────────────────────────────


def events_from_proposals(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        pid = str(n.get("id") or "")
        job = n.get("marketplaceJobPosting") or {}
        job_id = str(job.get("id") or "")
        audit = n.get("auditDetails") or {}
        created = _iso(audit.get("createdDateTime"))
        modified = _iso(audit.get("modifiedDateTime")) or created
        if not pid or not job_id or not created:
            continue
        status = ((n.get("status") or {}).get("status") or "").upper()
        rate = (n.get("terms") or {}).get("chargeRate") or {}
        try:
            bid_amount = (
                float(rate.get("rawValue")) if rate.get("rawValue") not in (None, "") else None
            )
        except (TypeError, ValueError):
            bid_amount = None
        contract = ((job.get("contractTerms") or {}).get("contractType") or "").upper()
        bid_type = "hourly" if contract == "HOURLY" else "fixed" if contract == "FIXED" else None
        meta = {
            "status": status,
            "proposal_id": pid,
            "modified": modified,
            "modified_ms": _raw_ms(audit.get("modifiedDateTime")),
            "currency": rate.get("currency") or "",
        }
        out.append(
            {
                "event_id": _event_id(pid, "SUBMITTED"),
                "job_id": job_id,
                "event": "SUBMITTED",
                "ts": created,
                "source": "api",
                "bid_amount": bid_amount,
                "bid_type": bid_type,
                "meta": meta,
            }
        )
        if n.get("viewedByClient"):
            out.append(
                {
                    "event_id": _event_id(pid, "VIEWED"),
                    "job_id": job_id,
                    "event": "VIEWED",
                    "ts": modified,
                    "source": "api",
                    "meta": meta,
                }
            )
        follow = (
            "HIRED" if status in HIRED_STATUSES else "LOST" if status in LOST_STATUSES else None
        )
        if follow:
            out.append(
                {
                    "event_id": _event_id(pid, follow),
                    "job_id": job_id,
                    "event": follow,
                    "ts": modified,
                    "source": "api",
                    "meta": meta,
                }
            )
    return out


def high_water_mark() -> int | None:
    """Newest proposal modification (epoch ms) covered by a complete crawl, or None."""
    raw = get_state(STATE_KEY)
    return int(raw) if raw and raw.isdigit() else None


def _newest_modified(nodes: list[dict]) -> int | None:
    stamps = [_raw_ms((n.get("auditDetails") or {}).get("modifiedDateTime")) for n in nodes]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def sync_vendor_proposals(
    *,
    transport: Transport | None = None,
    token: str | None = None,
    probe_path=None,
    force: bool = False,
    full: bool = False,
    max_pages: int = DEFAULT_MAX_PAGES,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    enabled, reason = capability(probe_path)
    if not enabled and not force:
        return {"skipped": reason, "seen": 0, "inserted": 0, "proposals": 0}
    since_ms = None if full else high_water_mark()
    nodes, complete = fetch_vendor_proposals(
        transport=transport, token=token, since_ms=since_ms, max_pages=max_pages, sleep=sleep
    )
    events = events_from_proposals(nodes)
    inserted = insert_events(events)  # whatever was fetched is kept, complete or not
    newest = _newest_modified(nodes)
    if complete and newest is not None:
        set_state(STATE_KEY, str(max(newest, since_ms or 0)))
    return {
        "skipped": None,
        "seen": len(events),
        "inserted": inserted,
        "proposals": len(nodes),
        "incremental": since_ms is not None,
        "complete": complete,
    }
