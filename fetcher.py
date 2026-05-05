import json
import time
from datetime import datetime, timedelta, timezone

import requests
from rich.console import Console

from auth import get_access_token
from config import (
    FETCH_BACKOFF_BASE,
    FETCH_BACKOFF_MAX,
    FETCH_MAX_RETRIES,
    GRAPHQL_URL,
)
from core.logging_setup import get_logger
from db import finish_fetch_run, start_fetch_run, upsert_jobs

console = Console()
log = get_logger(__name__)


# HTTP statuses that warrant a retry with backoff.
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _backoff_seconds(attempt: int) -> float:
    """Exponential: 1, 2, 4, 8 ... capped at FETCH_BACKOFF_MAX."""
    return min(FETCH_BACKOFF_BASE * (2 ** attempt), FETCH_BACKOFF_MAX)


def _post_with_retry(url: str, payload: dict, headers: dict, page: int) -> dict | None:
    """POST with exponential backoff on transient failures.

    Returns parsed JSON, or None to signal "give up on this page".
    """
    last_error: str = ""
    for attempt in range(FETCH_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code in _RETRY_STATUSES:
                last_error = f"HTTP {resp.status_code}"
                if attempt < FETCH_MAX_RETRIES:
                    delay = _backoff_seconds(attempt)
                    log.warning(
                        "Page %d attempt %d/%d hit %s — retrying in %.1fs",
                        page, attempt + 1, FETCH_MAX_RETRIES + 1, last_error, delay,
                    )
                    console.print(
                        f"  [yellow]{last_error} on page {page}, "
                        f"backoff {delay:.1f}s (attempt {attempt + 1}/{FETCH_MAX_RETRIES + 1})[/yellow]"
                    )
                    time.sleep(delay)
                    continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            log.error("HTTP error on page %d: %s", page, e)
            console.print(f"[red]HTTP error page {page}: {e}[/red]")
            return None
        except requests.RequestException as e:
            last_error = type(e).__name__
            if attempt < FETCH_MAX_RETRIES:
                delay = _backoff_seconds(attempt)
                log.warning(
                    "Page %d attempt %d/%d %s — retrying in %.1fs",
                    page, attempt + 1, FETCH_MAX_RETRIES + 1, last_error, delay,
                )
                console.print(
                    f"  [yellow]{last_error} on page {page}, "
                    f"backoff {delay:.1f}s (attempt {attempt + 1}/{FETCH_MAX_RETRIES + 1})[/yellow]"
                )
                time.sleep(delay)
                continue
            log.error("Page %d gave up after %d attempts: %s", page, FETCH_MAX_RETRIES + 1, e)
            console.print(f"[red]Page {page} failed after retries: {e}[/red]")
            return None
    log.error("Page %d exhausted retries (%s)", page, last_error)
    console.print(f"[red]Page {page} exhausted retries ({last_error})[/red]")
    return None


def _get_org_id(token: str) -> str:
    r = requests.post(
        GRAPHQL_URL,
        json={"query": "{ organization { id } }"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=10,
    )
    return (r.json().get("data") or {}).get("organization", {}).get("id", "1")


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


def fetch_jobs(
    search_term: str = "",
    category: str = "",
    limit: int = 500,
    since_days: int | None = None,
) -> int:
    token = get_access_token()
    org_id = _get_org_id(token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Upwork-API-TenantId": org_id,
    }

    cutoff_iso = None
    if since_days is not None:
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=since_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

    total_fetched = 0
    total_new = 0
    offset = 0
    page_size = 50
    page = 1
    run_id = start_fetch_run(search_term=search_term, category=category)

    while total_fetched < limit:
        batch = min(page_size, limit - total_fetched)
        job_filter = {
            "pagination_eq": {"first": batch, "after": str(offset)},
        }
        if search_term:
            job_filter["searchExpression_eq"] = search_term

        payload = {
            "query": SEARCH_QUERY,
            "variables": {"filter": job_filter},
        }

        data = _post_with_retry(GRAPHQL_URL, payload, headers, page)
        if data is None:
            break

        if "errors" in data:
            console.print(f"[red]GraphQL errors: {json.dumps(data['errors'], indent=2)}[/red]")
            break

        result = (data.get("data") or {}).get("marketplaceJobPostingsSearch") or {}
        edges = result.get("edges") or []
        page_info = result.get("pageInfo") or {}

        if not edges:
            break

        jobs = [_parse_job(e["node"], category) for e in edges if e.get("node")]
        seen, new = upsert_jobs(jobs, search_term=search_term)
        total_fetched += seen
        total_new    += new
        offset       += seen

        console.print(
            f"  Page {page:>3}: [cyan]{seen}[/cyan] jobs ({new} new)  "
            f"([green]{total_fetched}[/green] total · {total_new} new)"
        )

        # Early-stop when sorted-by-recency results pass the cutoff window
        if cutoff_iso and jobs and jobs[-1]["published_at"] < cutoff_iso:
            console.print(
                f"  [dim]Reached jobs older than {since_days}d cutoff — stopping.[/dim]"
            )
            break

        if not page_info.get("hasNextPage"):
            break

        page += 1
        time.sleep(0.4)

    finish_fetch_run(run_id, jobs_seen=total_fetched, jobs_new=total_new, status="done")
    return total_fetched


def _parse_job(node: dict, override_category: str = "") -> dict:
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

    category = override_category or (node.get("category") or "Uncategorized")

    # Client fields
    client = node.get("client") or {}
    spent_raw = (client.get("totalSpent") or {}).get("rawValue") or 0
    verified = 1 if client.get("verificationStatus") == "VERIFIED" else 0
    country = (client.get("location") or {}).get("country") or ""

    return {
        "id":                  node.get("id") or "",
        "title":               node.get("title") or "",
        "published_at":        (node.get("publishedDateTime") or "").replace("+0000", "").replace("Z", ""),
        "category":            category,
        "contractor_tier":     node.get("experienceLevel") or "INTERMEDIATE",
        "budget_type":         budget_type,
        "budget_amount":       round(budget_amount, 2),
        "budget_min":          round(budget_min, 2),
        "budget_max":          round(budget_max, 2),
        "skills":              json.dumps(skills),
        "total_applicants":    node.get("totalApplicants") or 0,
        "client_total_hires":  int(client.get("totalHires") or 0),
        "client_total_spent":  float(spent_raw),
        "client_verified":     verified,
        "client_feedback":     float(client.get("totalFeedback") or 0),
        "client_country":      country,
        "is_premium":          1 if node.get("premium") else 0,
        "is_enterprise":       1 if node.get("enterprise") else 0,
        "duration_label":      node.get("durationLabel") or "",
        "description":         node.get("description") or "",
    }
