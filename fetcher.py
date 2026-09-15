"""Upwork GraphQL client and job-search fetcher.

Behaviour (ported from the proposal agent's client, which has run against
the live API for months):

  * HTTP 200 with a populated `errors[]` is Upwork's *normal* failure mode.
    When `data` is also present and the error is not a permission or
    validation failure, the partial data is used: a nulled non-null field
    (e.g. `experienceLevel`) kills one node, not the whole page.
  * HTTP 401/403, or an `errors[]` message that looks like a rejected token,
    refreshes the access token once and retries.
  * HTTP 429/5xx and connection errors back off with full jitter.
  * Every call passes through a shared token bucket (5 req/s, burst 10).
  * The HTTP layer is an injectable `transport`, so tests and `--offline`
    modes replay recorded responses without touching the network.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from rich.console import Console

import auth
from config import FETCH_BACKOFF_BASE, FETCH_BACKOFF_MAX, FETCH_MAX_RETRIES, GRAPHQL_URL
from core.logging_setup import get_logger
from core.ratelimit import TokenBucket
from db import finish_fetch_run, start_fetch_run, upsert_jobs

console = Console()
log = get_logger(__name__)

PAGE_SIZE = 50

# HTTP statuses that warrant a retry with backoff.
_RETRY_STATUSES = {429, 500, 502, 503, 504}

# Shared outbound limiter for every GraphQL call made by this process.
_bucket = TokenBucket(rate_per_sec=5.0, burst=10)


# ─── Transport layer ─────────────────────────────────────────────────────────


@dataclass
class Response:
    """The minimal HTTP response shape the client needs."""

    status_code: int
    payload: Any = None  # parsed JSON body, or None when the body was not JSON
    text: str = ""


Transport = Callable[[dict, dict], Response]  # (json_body, headers) -> Response


def requests_transport(body: dict, headers: dict) -> Response:
    """Default transport: a real POST to the Upwork GraphQL endpoint."""
    resp = requests.post(GRAPHQL_URL, json=body, headers=headers, timeout=30)
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return Response(resp.status_code, payload, resp.text[:500])


class FixtureTransport:
    """Replays recorded responses keyed by GraphQL operation name.

    Keys are `"<OperationName>"`, or `"<OperationName>:<name variable>"` when
    the variables carry a `name` (the introspection query is reused for many
    types). A fixture entry is either a raw payload (served with HTTP 200) or
    `{"status": <int>, "payload": {...}}`.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    @staticmethod
    def key_for(body: dict) -> str:
        m = re.search(r"\b(?:query|mutation)\s+(\w+)", body.get("query") or "")
        op = m.group(1) if m else "anonymous"
        name = (body.get("variables") or {}).get("name")
        return f"{op}:{name}" if name else op

    def __call__(self, body: dict, headers: dict) -> Response:
        key = self.key_for(body)
        self.calls.append(body)
        if key not in self.responses:
            raise KeyError(f"No fixture response recorded for {key!r}")
        entry = self.responses[key]
        if isinstance(entry, dict) and set(entry) == {"status", "payload"}:
            return Response(int(entry["status"]), entry["payload"])
        return Response(200, entry)


# ─── Errors ─────────────────────────────────────────────────────────────────


class GraphQLError(Exception):
    """Raised when the API returns GraphQL `errors[]` we cannot work around."""

    def __init__(self, errors: list[dict[str, Any]], data: Any = None) -> None:
        self.errors = errors or []
        self.data = data
        msg = "; ".join(str(e.get("message", e)) for e in self.errors[:3]) or "GraphQL error"
        super().__init__(msg)

    @property
    def is_permission_error(self) -> bool:
        return any("oauth2 permissions" in (e.get("message") or "").lower() for e in self.errors)

    @property
    def is_validation_error(self) -> bool:
        return any(
            (e.get("extensions") or {}).get("classification") == "ValidationError"
            for e in self.errors
        )


class TransientError(Exception):
    """Raised when retries on 429/5xx/connection failures are exhausted."""


def _looks_like_auth_error(errors: list[dict]) -> bool:
    blob = " ".join((e.get("message") or "").lower() for e in errors)
    return "invalid token" in blob or "unauthorized" in blob or "expired" in blob


def _backoff_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff, capped at FETCH_BACKOFF_MAX."""
    return random.uniform(0, min(FETCH_BACKOFF_MAX, FETCH_BACKOFF_BASE * (2**attempt)))


# ─── Core call ──────────────────────────────────────────────────────────────


def gql(
    query: str,
    variables: dict | None = None,
    *,
    transport: Transport | None = None,
    org_id: str = "",
    token: str | None = None,
    max_retries: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Execute one GraphQL operation and return its `data` object.

    Raises `GraphQLError` for non-recoverable API errors and `TransientError`
    when retries are exhausted. Auth failures surface as `RuntimeError` from
    `auth` (the message tells the user to run `make auth`).
    """
    transport = transport or requests_transport
    retries = FETCH_MAX_RETRIES if max_retries is None else max_retries
    body: dict[str, Any] = {"query": query}
    if variables is not None:
        body["variables"] = variables
    token = token or auth.get_access_token()

    attempt = 0
    did_refresh = False
    while True:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if org_id:
            headers["X-Upwork-API-TenantId"] = org_id

        _bucket.acquire()
        try:
            resp = transport(body, headers)
        except requests.RequestException as exc:
            if attempt >= retries:
                raise TransientError(f"{type(exc).__name__}: {exc}") from exc
            delay = _backoff_seconds(attempt)
            log.warning(
                "%s — retry %d/%d in %.1fs", type(exc).__name__, attempt + 1, retries, delay
            )
            sleep(delay)
            attempt += 1
            continue

        if resp.status_code in _RETRY_STATUSES:
            if attempt >= retries:
                raise TransientError(f"HTTP {resp.status_code} after {attempt + 1} attempts")
            delay = _backoff_seconds(attempt)
            log.warning(
                "HTTP %s — retry %d/%d in %.1fs", resp.status_code, attempt + 1, retries, delay
            )
            sleep(delay)
            attempt += 1
            continue

        if resp.status_code in (401, 403):
            if not did_refresh:
                log.info("HTTP %s — refreshing access token and retrying once", resp.status_code)
                token = auth.force_refresh()
                did_refresh = True
                continue
            raise GraphQLError(
                [
                    {
                        "message": (
                            f"HTTP {resp.status_code} from api.upwork.com even after refreshing "
                            "the access token — run `make auth` to re-authorize."
                        )
                    }
                ]
            )

        if resp.status_code >= 400:
            raise GraphQLError([{"message": f"HTTP {resp.status_code}: {resp.text[:200]}"}])

        payload = resp.payload if isinstance(resp.payload, dict) else {}
        errors = payload.get("errors")
        if errors:
            if not did_refresh and _looks_like_auth_error(errors):
                log.info("Token rejected mid-call — refreshing and retrying once")
                token = auth.force_refresh()
                did_refresh = True
                continue
            err = GraphQLError(errors, data=payload.get("data"))
            if (
                payload.get("data") is not None
                and not err.is_permission_error
                and not err.is_validation_error
            ):
                # Routine: Upwork nulls a non-null field on one node and returns the
                # rest of the page. The parser skips the nulled nodes.
                log.debug(
                    "Partial GraphQL result — %d field error(s); using returned data. First: %s",
                    len(errors),
                    (errors[0].get("message") or "")[:160],
                )
                return payload["data"]
            raise err

        return payload.get("data") or {}


def get_org_id(*, transport: Transport | None = None, token: str | None = None) -> str:
    """Resolve the tenant id for the `X-Upwork-API-TenantId` header ("" if unknown)."""
    try:
        data = gql("query OrgId { organization { id } }", transport=transport, token=token)
    except GraphQLError as exc:
        log.warning("Could not resolve organization id (%s); continuing without tenant header", exc)
        return ""
    return str(((data or {}).get("organization") or {}).get("id") or "")


# ─── Job search ─────────────────────────────────────────────────────────────

SEARCH_QUERY = """
query SearchJobs($filter: MarketplaceJobPostingsSearchFilter) {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: $filter
    searchType: USER_JOBS_SEARCH
    sortAttributes: [{field: RECENCY}]
  ) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        title
        description
        publishedDateTime
        experienceLevel
        category
        durationLabel
        premium
        enterprise
        totalApplicants
        skills { name }
        amount { rawValue }
        hourlyBudgetMin { rawValue }
        hourlyBudgetMax { rawValue }
        client {
          totalHires
          totalPostedJobs
          totalSpent { rawValue }
          verificationStatus
          totalFeedback
          location { country }
        }
      }
    }
  }
}
"""


def build_filter(
    search_term: str = "",
    category_ids: list[str] | None = None,
    subcategory_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    *,
    first: int = PAGE_SIZE,
    after: str = "0",
) -> dict:
    """Server-side filter for `marketplaceJobPostingsSearch`.

    Field names were confirmed by introspection in the proposal agent
    (`upwork/jobs.py::build_filter`). `pagination_eq` is offset-style: the
    first page must use after="0".
    """
    f: dict[str, Any] = {"pagination_eq": {"first": first, "after": after}}
    if search_term:
        f["searchExpression_eq"] = search_term
    if category_ids:
        f["categoryIds_any"] = list(category_ids)
    if subcategory_ids:
        f["subcategoryIds_any"] = list(subcategory_ids)
    if skill_ids:
        # The schema only exposes `ontologySkillIds_all` (match ALL listed skills).
        f["ontologySkillIds_all"] = list(skill_ids)
    return f


def fetch_jobs(
    search_term: str = "",
    *,
    category_ids: list[str] | None = None,
    subcategory_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    limit: int = 500,
    since_days: int | None = None,
    label: str | None = None,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Page through a search, upsert every job, and record a `fetch_runs` row.

    Returns the number of jobs written. Nodes the API nulled are skipped and
    counted. API failures mark the run `error` and re-raise, so callers such as
    `sync` can record them; interactive callers catch and continue.
    """
    token = auth.get_access_token()  # fail fast before opening a run
    org_id = get_org_id(transport=transport, token=token)

    cutoff_iso = None
    if since_days is not None:
        cutoff_iso = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%S")

    run_label = label or search_term
    run_id = start_fetch_run(search_term=run_label, category=",".join(category_ids or []))

    total_fetched = 0
    total_new = 0
    dropped = 0
    offset = 0
    page = 1
    status = "done"

    try:
        while total_fetched < limit:
            batch = min(PAGE_SIZE, limit - total_fetched)
            job_filter = build_filter(
                search_term,
                category_ids,
                subcategory_ids,
                skill_ids,
                first=batch,
                after=str(offset),
            )
            data = gql(
                SEARCH_QUERY,
                {"filter": job_filter},
                transport=transport,
                org_id=org_id,
                token=token,
                sleep=sleep,
            )

            result = data.get("marketplaceJobPostingsSearch") or {}
            edges = result.get("edges") or []
            page_info = result.get("pageInfo") or {}
            if not edges:
                break

            nodes = [e.get("node") for e in edges if isinstance(e, dict)]
            jobs = [_parse_job(n) for n in nodes if n]
            dropped += len(nodes) - len(jobs)

            seen, new = upsert_jobs(jobs, search_term=run_label) if jobs else (0, 0)
            total_fetched += seen
            total_new += new
            # Advance by edges, not by parsed jobs: nulled nodes still occupy offsets.
            offset += len(edges)

            console.print(
                f"  Page {page:>3}: [cyan]{seen}[/cyan] jobs ({new} new)  "
                f"([green]{total_fetched}[/green] total · {total_new} new)"
            )

            # Early-stop when sorted-by-recency results pass the cutoff window.
            if cutoff_iso and jobs and jobs[-1]["published_at"] < cutoff_iso:
                console.print(
                    f"  [dim]Reached jobs older than {since_days}d cutoff — stopping.[/dim]"
                )
                break
            if not page_info.get("hasNextPage"):
                break
            page += 1
    except (GraphQLError, TransientError) as exc:
        status = "error"
        log.error("Fetch %r failed on page %d: %s", run_label, page, exc)
        console.print(f"  [red]Fetch failed on page {page}:[/red] {exc}")
        raise
    finally:
        if dropped:
            log.info("Skipped %d node(s) the API returned as null", dropped)
        finish_fetch_run(run_id, jobs_seen=total_fetched, jobs_new=total_new, status=status)

    return total_fetched


def _parse_job(node: dict) -> dict:
    """Flatten one search node into the `jobs` row shape."""
    skills = [s["name"] for s in (node.get("skills") or []) if (s or {}).get("name")]

    # Budget parsing
    amount_raw = float((node.get("amount") or {}).get("rawValue") or 0)
    min_raw = (node.get("hourlyBudgetMin") or {}).get("rawValue")
    max_raw = (node.get("hourlyBudgetMax") or {}).get("rawValue")

    if min_raw is not None or max_raw is not None:
        budget_type = "HOURLY"
        budget_min = float(min_raw or 0)
        budget_max = float(max_raw or 0)
        budget_amount = (budget_min + budget_max) / 2 if (budget_min + budget_max) > 0 else 0
    elif amount_raw > 0:
        budget_type = "FIXED"
        budget_amount = amount_raw
        budget_min = amount_raw
        budget_max = amount_raw
    else:
        budget_type = "UNKNOWN"
        budget_amount = 0.0
        budget_min = 0.0
        budget_max = 0.0

    # Client fields
    client = node.get("client") or {}
    spent_raw = (client.get("totalSpent") or {}).get("rawValue") or 0
    verified = 1 if client.get("verificationStatus") == "VERIFIED" else 0
    country = (client.get("location") or {}).get("country") or ""

    return {
        "id": node.get("id") or "",
        "title": node.get("title") or "",
        "published_at": (node.get("publishedDateTime") or "").replace("+0000", "").replace("Z", ""),
        "category": node.get("category") or "Uncategorized",
        # Upwork sometimes nulls this non-null field; store the truth, not a guess.
        "contractor_tier": node.get("experienceLevel") or "UNKNOWN",
        "budget_type": budget_type,
        "budget_amount": round(budget_amount, 2),
        "budget_min": round(budget_min, 2),
        "budget_max": round(budget_max, 2),
        "skills": json.dumps(skills),
        "total_applicants": node.get("totalApplicants") or 0,
        "client_total_hires": int(client.get("totalHires") or 0),
        "client_total_spent": float(spent_raw),
        "client_verified": verified,
        "client_feedback": float(client.get("totalFeedback") or 0),
        "client_country": country,
        "is_premium": 1 if node.get("premium") else 0,
        "is_enterprise": 1 if node.get("enterprise") else 0,
        "duration_label": node.get("durationLabel") or "",
        "description": node.get("description") or "",
    }
