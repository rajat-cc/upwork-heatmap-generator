import json
import time

import requests
from rich.console import Console

from auth import get_access_token
from config import GRAPHQL_URL
from db import upsert_jobs

console = Console()


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


def fetch_jobs(search_term: str = "", category: str = "", limit: int = 500) -> int:
    token = get_access_token()
    org_id = _get_org_id(token)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Upwork-API-TenantId": org_id,
    }

    total_fetched = 0
    offset = 0
    page_size = 50
    page = 1

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

        try:
            resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                console.print("[yellow]Rate limited — waiting 10s...[/yellow]")
                time.sleep(10)
                continue
            console.print(f"[red]HTTP error page {page}: {e}[/red]")
            break
        except requests.RequestException as e:
            console.print(f"[red]Request failed page {page}: {e}[/red]")
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
        upsert_jobs(jobs)
        total_fetched += len(jobs)
        offset += len(jobs)

        console.print(
            f"  Page {page:>3}: [cyan]{len(jobs)}[/cyan] jobs fetched  "
            f"([green]{total_fetched}[/green] total)"
        )

        if not page_info.get("hasNextPage"):
            break

        page += 1
        time.sleep(0.4)

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
    }
