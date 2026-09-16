"""API capability probe.

Answers, for the OAuth key this repo uses, the questions the roadmap's gated
features depend on:

  * which input fields `MarketplaceJobPostingsSearchFilter` accepts,
  * what the search node and its `client` object expose (is there any stable
    client identity?),
  * whether `marketplaceJobPosting(id)` is reachable, and if so whether it
    carries activity counters (`activityStat.jobActivity`) and a stable client
    id (`clientCompanyPublic.id`) — the detail-stage snapshot source,
  * whether the `marketplaceJobPostingsContents(ids)` query works and whether
    it carries applicant/activity fields,
  * the `VendorProposalStatusName` enum and whether `vendorProposals` is readable.

Results are written to `docs/api_probe.json` so later phases can read the
answers instead of guessing. `--offline` replays a recorded fixture so CI can
exercise the code path without a token. Upwork returns transient
"Transform timeout" errors inside a 200 response now and then, so every step
is retried once before its failure is recorded.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

from core.logging_setup import get_logger
from fetcher import FixtureTransport, GraphQLError, TransientError, Transport, get_org_id, gql

console = Console()
log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "api_probe.json"
OFFLINE_FIXTURE = ROOT / "tests" / "fixtures" / "graphql" / "probe_offline.json"

RETRY_DELAY_SECONDS = 2.0

INTROSPECT_TYPE = """
query IntrospectType($name: String!) {
  __type(name: $name) {
    name
    kind
    enumValues { name }
    inputFields {
      name
      type { kind name ofType { kind name ofType { kind name } } }
    }
    fields {
      name
      type { kind name ofType { kind name ofType { kind name } } }
    }
  }
}
"""

SEARCH_TYPENAMES = """
query ProbeSearch {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: {pagination_eq: {first: 1, after: "0"}}
    searchType: USER_JOBS_SEARCH
    sortAttributes: [{field: RECENCY}]
  ) {
    edges { node { __typename id client { __typename } } }
  }
}
"""

DETAIL_PROBE = """
query ProbeDetail($id: ID!) {
  marketplaceJobPosting(id: $id) { id }
}
"""

CONTENTS_PROBE = """
query ProbeContents($ids: [ID!]!) {
  marketplaceJobPostingsContents(ids: $ids) { __typename id }
}
"""

# `filter.status_eq` and `sortAttribute` are required arguments; one status is
# enough to learn whether the key holds the proposals scope at all.
VENDOR_PROPOSALS_PROBE = """
query ProbeProposals {
  vendorProposals(
    filter: {status_eq: Pending}
    sortAttribute: {field: CREATEDDATETIME, sortOrder: DESC}
    pagination: {first: 1}
  ) { pageInfo { hasNextPage } }
}
"""

# Field names that would give a client a stable identity across postings.
_IDENTITY_FIELDS = {"id", "rid", "nid", "companyName", "company", "publicCompanyInfo"}
# Field names that would let the contents query serve as a snapshot source.
_ACTIVITY_FIELDS = {"totalApplicants", "activityStat", "hiredCount", "invitesSent"}
# On the detail type: the activity block and the public company (stable client id).
DETAIL_TYPE = "MarketplaceJobPosting"
DETAIL_ACTIVITY_TYPE = "JobActivity"
_DETAIL_ACTIVITY_FIELD = "activityStat"
_DETAIL_CLIENT_FIELD = "clientCompanyPublic"


def _type_name(t: dict | None) -> str:
    """Render an introspection type ref like `[String!]!`."""
    if not t:
        return "?"
    kind, name, inner = t.get("kind"), t.get("name"), t.get("ofType")
    if kind == "NON_NULL":
        return _type_name(inner) + "!"
    if kind == "LIST":
        return "[" + _type_name(inner) + "]"
    return name or "?"


def _introspect(name: str, **call) -> dict[str, Any]:
    data = gql(INTROSPECT_TYPE, {"name": name}, **call)
    t = data.get("__type") or {}
    return {
        "name": t.get("name"),
        "kind": t.get("kind"),
        "enum_values": [e["name"] for e in (t.get("enumValues") or [])],
        "input_fields": {
            f["name"]: _type_name(f.get("type")) for f in (t.get("inputFields") or [])
        },
        "fields": {f["name"]: _type_name(f.get("type")) for f in (t.get("fields") or [])},
    }


def _check(
    name: str,
    fn,
    results: dict[str, Any],
    *,
    retries: int = 1,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Run one probe step, recording success or the error verbatim.

    Permission and validation errors are answers, so they are recorded at once;
    anything else (Upwork's transient "Transform timeout", a 5xx, a dropped
    connection) is retried `retries` times first.
    """
    attempt = 0
    while True:
        try:
            details = fn()
        except GraphQLError as exc:
            retryable = not exc.is_permission_error and not exc.is_validation_error
            if retryable and attempt < retries:
                attempt += 1
                log.info("Probe step %s failed (%s); retrying", name, exc)
                sleep(RETRY_DELAY_SECONDS)
                continue
            results[name] = {
                "ok": False,
                "error": str(exc),
                "permission_error": exc.is_permission_error,
                "details": None,
                "retried": attempt,
            }
            return None
        except TransientError as exc:
            if attempt < retries:
                attempt += 1
                sleep(RETRY_DELAY_SECONDS)
                continue
            results[name] = {
                "ok": False,
                "error": str(exc),
                "permission_error": False,
                "details": None,
                "retried": attempt,
            }
            return None
        except KeyError as exc:
            results[name] = {
                "ok": False,
                "error": str(exc),
                "permission_error": False,
                "details": None,
                "retried": attempt,
            }
            return None
        results[name] = {
            "ok": True,
            "error": None,
            "permission_error": False,
            "details": details,
            "retried": attempt,
        }
        return details


def run_probe(
    *,
    offline: bool = False,
    out: Path | str | None = None,
    transport: Transport | None = None,
    quiet: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute every probe step; write and return the JSON report."""
    if offline and transport is None:
        transport = FixtureTransport(load_offline_fixture())
    token = "offline" if offline else None
    call: dict[str, Any] = {"transport": transport, "token": token}
    org_id = get_org_id(**call)
    call["org_id"] = org_id

    checks: dict[str, Any] = {}

    def check(name: str, fn):
        return _check(name, fn, checks, sleep=sleep)

    filt = check("filter_fields", lambda: _introspect("MarketplaceJobPostingsSearchFilter", **call))

    def _search():
        data = gql(SEARCH_TYPENAMES, **call)
        edges = (data.get("marketplaceJobPostingsSearch") or {}).get("edges") or []
        node = (edges[0] or {}).get("node") if edges else None
        if not node:
            raise GraphQLError([{"message": "search returned no nodes"}])
        return {
            "node_type": node.get("__typename"),
            "client_type": (node.get("client") or {}).get("__typename"),
            "sample_id": node.get("id"),
        }

    search = check("search_types", _search)
    sample_id = (search or {}).get("sample_id")

    node_fields = client_fields = None
    if search:
        if search.get("node_type"):
            node_fields = check(
                "search_node_fields", lambda: _introspect(search["node_type"], **call)
            )
        if search.get("client_type"):
            client_fields = check(
                "search_client_fields", lambda: _introspect(search["client_type"], **call)
            )

    detail = None
    detail_fields = detail_activity = None
    contents = None
    contents_fields = None
    if sample_id:
        detail = check(
            "detail_query",
            lambda: gql(DETAIL_PROBE, {"id": sample_id}, **call).get("marketplaceJobPosting"),
        )
        if detail:
            detail_fields = check("detail_fields", lambda: _introspect(DETAIL_TYPE, **call))
            if _DETAIL_ACTIVITY_FIELD in ((detail_fields or {}).get("fields") or {}):
                detail_activity = check(
                    "detail_activity_fields", lambda: _introspect(DETAIL_ACTIVITY_TYPE, **call)
                )

        def _contents():
            data = gql(CONTENTS_PROBE, {"ids": [sample_id]}, **call)
            items = data.get("marketplaceJobPostingsContents") or []
            return {"type": (items[0] or {}).get("__typename") if items else None, "n": len(items)}

        contents = check("contents_query", _contents)
        if contents and contents.get("type"):
            contents_fields = check(
                "contents_fields", lambda: _introspect(contents["type"], **call)
            )

    statuses = check("proposal_statuses", lambda: _introspect("VendorProposalStatusName", **call))
    proposals = check(
        "vendor_proposals", lambda: gql(VENDOR_PROPOSALS_PROBE, **call).get("vendorProposals")
    )

    client_field_names = set((client_fields or {}).get("fields") or {})
    contents_field_names = set((contents_fields or {}).get("fields") or {})
    detail_field_names = set((detail_fields or {}).get("fields") or {})
    detail_activity_names = set((detail_activity or {}).get("fields") or {})
    capabilities = {
        "filter_fields": sorted((filt or {}).get("input_fields") or {}),
        "search_node_fields": sorted((node_fields or {}).get("fields") or {}),
        "client_fields": sorted(client_field_names),
        "client_identity_fields": sorted(client_field_names & _IDENTITY_FIELDS),
        "detail_query": bool(detail),
        "detail_fields": sorted(detail_field_names),
        "detail_activity_fields": sorted(detail_activity_names),
        "detail_has_activity": bool(detail_activity_names),
        "detail_client_identity": _DETAIL_CLIENT_FIELD in detail_field_names,
        "contents_query": bool(contents),
        "contents_fields": sorted(contents_field_names),
        "contents_has_activity": bool(contents_field_names & _ACTIVITY_FIELDS),
        "proposal_statuses": (statuses or {}).get("enum_values") or [],
        "vendor_proposals": bool(proposals),
    }

    report = {
        "probed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "offline": offline,
        "org_id": org_id,
        "checks": checks,
        "capabilities": capabilities,
    }

    out_path = Path(out) if out else DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    if not quiet:
        render(report, out_path)
    return report


def load_offline_fixture(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else OFFLINE_FIXTURE
    return json.loads(p.read_text())


def render(report: dict[str, Any], out_path: Path) -> None:
    caps = report["capabilities"]
    checks = report["checks"]
    table = Table(
        title="API capability probe" + ("  ·  OFFLINE FIXTURE" if report["offline"] else ""),
        box=box.ROUNDED,
        title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column("Check", style="bold white", min_width=22)
    table.add_column("Result", min_width=8)
    table.add_column("Notes", style="bright_black", ratio=1)

    def row(label: str, ok: bool | None, note: str) -> None:
        mark = (
            "[green]yes[/green]"
            if ok
            else ("[red]no[/red]" if ok is False else "[yellow]?[/yellow]")
        )
        table.add_row(label, mark, note)

    def error_of(name: str) -> str | None:
        return (checks.get(name) or {}).get("error")

    row(
        "Search filter fields", bool(caps["filter_fields"]), ", ".join(caps["filter_fields"]) or "—"
    )
    row(
        "Client identity on search",
        bool(caps["client_identity_fields"]),
        ", ".join(caps["client_identity_fields"]) or "no id/company field on the search node",
    )
    row(
        "marketplaceJobPosting(id)",
        caps["detail_query"],
        error_of("detail_query") or "detail query reachable",
    )
    row(
        "Activity fields on detail",
        caps["detail_has_activity"] if caps["detail_query"] else None,
        (
            "detail-stage snapshots enabled: " + ", ".join(caps["detail_activity_fields"])
            if caps["detail_has_activity"]
            else "no activityStat on the detail type → snapshots stay search-only"
        ),
    )
    row(
        "Client identity on detail",
        caps["detail_client_identity"] if caps["detail_query"] else None,
        (
            "clientCompanyPublic.id is stored for detail-fetched jobs"
            if caps["detail_client_identity"]
            else "no public company object → clients stay heuristic"
        ),
    )
    row(
        "marketplaceJobPostingsContents",
        caps["contents_query"],
        error_of("contents_query") or "contents query reachable",
    )
    row(
        "Activity fields on contents",
        caps["contents_has_activity"],
        "snapshots can use the contents query"
        if caps["contents_has_activity"]
        else "contents carries text only",
    )
    row(
        "vendorProposals readable",
        caps["vendor_proposals"],
        error_of("vendor_proposals") or f"statuses: {', '.join(caps['proposal_statuses']) or '—'}",
    )
    console.print(table)
    console.print(f"  [dim]Report written to[/dim] [cyan]{out_path}[/cyan]\n")
