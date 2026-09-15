"""API capability probe.

Answers, for the OAuth key this repo uses, the questions the roadmap's gated
features depend on:

  * which input fields `MarketplaceJobPostingsSearchFilter` accepts,
  * what the search node and its `client` object expose (is there any stable
    client identity?),
  * whether `marketplaceJobPosting(id)` is reachable, or only the
    `marketplaceJobPostingsContents(ids)` query,
  * whether the contents query carries applicant/activity fields,
  * the `VendorProposalStatusName` enum and whether `vendorProposals` is readable.

Results are written to `docs/api_probe.json` so later phases can read the
answers instead of guessing. `--offline` replays a recorded fixture so CI can
exercise the code path without a token.
"""

from __future__ import annotations

import json
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

VENDOR_PROPOSALS_PROBE = """
query ProbeProposals {
  vendorProposals(pagination: {first: 1}) { pageInfo { hasNextPage } }
}
"""

# Field names that would give a client a stable identity across postings.
_IDENTITY_FIELDS = {"id", "rid", "nid", "companyName", "company", "publicCompanyInfo"}
# Field names that would let the contents query serve as a snapshot source.
_ACTIVITY_FIELDS = {"totalApplicants", "activityStat", "hiredCount", "invitesSent"}


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


def _check(name: str, fn, results: dict[str, Any]) -> dict[str, Any] | None:
    """Run one probe step, recording success or the error verbatim."""
    try:
        details = fn()
    except GraphQLError as exc:
        results[name] = {
            "ok": False,
            "error": str(exc),
            "permission_error": exc.is_permission_error,
            "details": None,
        }
        return None
    except (TransientError, KeyError) as exc:
        results[name] = {"ok": False, "error": str(exc), "permission_error": False, "details": None}
        return None
    results[name] = {"ok": True, "error": None, "permission_error": False, "details": details}
    return details


def run_probe(
    *,
    offline: bool = False,
    out: Path | str | None = None,
    transport: Transport | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Execute every probe step; write and return the JSON report."""
    if offline and transport is None:
        transport = FixtureTransport(load_offline_fixture())
    token = "offline" if offline else None
    call: dict[str, Any] = {"transport": transport, "token": token}
    org_id = get_org_id(**call)
    call["org_id"] = org_id

    checks: dict[str, Any] = {}

    filt = _check(
        "filter_fields", lambda: _introspect("MarketplaceJobPostingsSearchFilter", **call), checks
    )

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

    search = _check("search_types", _search, checks)
    sample_id = (search or {}).get("sample_id")

    node_fields = client_fields = None
    if search:
        if search.get("node_type"):
            node_fields = _check(
                "search_node_fields", lambda: _introspect(search["node_type"], **call), checks
            )
        if search.get("client_type"):
            client_fields = _check(
                "search_client_fields", lambda: _introspect(search["client_type"], **call), checks
            )

    detail = None
    contents = None
    contents_fields = None
    if sample_id:
        detail = _check(
            "detail_query",
            lambda: gql(DETAIL_PROBE, {"id": sample_id}, **call).get("marketplaceJobPosting"),
            checks,
        )

        def _contents():
            data = gql(CONTENTS_PROBE, {"ids": [sample_id]}, **call)
            items = data.get("marketplaceJobPostingsContents") or []
            return {"type": (items[0] or {}).get("__typename") if items else None, "n": len(items)}

        contents = _check("contents_query", _contents, checks)
        if contents and contents.get("type"):
            contents_fields = _check(
                "contents_fields", lambda: _introspect(contents["type"], **call), checks
            )

    statuses = _check(
        "proposal_statuses", lambda: _introspect("VendorProposalStatusName", **call), checks
    )
    proposals = _check(
        "vendor_proposals",
        lambda: gql(VENDOR_PROPOSALS_PROBE, **call).get("vendorProposals"),
        checks,
    )

    client_field_names = set((client_fields or {}).get("fields") or {})
    contents_field_names = set((contents_fields or {}).get("fields") or {})
    capabilities = {
        "filter_fields": sorted((filt or {}).get("input_fields") or {}),
        "search_node_fields": sorted((node_fields or {}).get("fields") or {}),
        "client_fields": sorted(client_field_names),
        "client_identity_fields": sorted(client_field_names & _IDENTITY_FIELDS),
        "detail_query": bool(detail),
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

    row(
        "Search filter fields", bool(caps["filter_fields"]), ", ".join(caps["filter_fields"]) or "—"
    )
    row(
        "Client identity on search",
        bool(caps["client_identity_fields"]),
        ", ".join(caps["client_identity_fields"])
        or "no id/company field → clients table stays deferred",
    )
    row(
        "marketplaceJobPosting(id)",
        caps["detail_query"],
        (report["checks"].get("detail_query") or {}).get("error") or "detail query reachable",
    )
    row(
        "marketplaceJobPostingsContents",
        caps["contents_query"],
        (report["checks"].get("contents_query") or {}).get("error") or "contents query reachable",
    )
    row(
        "Activity fields on contents",
        caps["contents_has_activity"],
        "snapshots can use the contents query"
        if caps["contents_has_activity"]
        else "snapshots fall back to search re-observation",
    )
    row(
        "vendorProposals readable",
        caps["vendor_proposals"],
        (report["checks"].get("vendor_proposals") or {}).get("error")
        or f"statuses: {', '.join(caps['proposal_statuses']) or '—'}",
    )
    console.print(table)
    console.print(f"  [dim]Report written to[/dim] [cyan]{out_path}[/cyan]\n")
