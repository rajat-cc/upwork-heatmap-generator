"""Classification + opportunity scoring for n8n jobs."""
from __future__ import annotations

from core.models import Job
from db import save_classifications
from taxonomies.compile import compile_taxonomy, match_categories
from taxonomies.n8n import INDUSTRIES, STACKS, WORKFLOWS

INDUSTRIES_RE = compile_taxonomy(INDUSTRIES)
WORKFLOWS_RE  = compile_taxonomy(WORKFLOWS)
STACKS_RE     = compile_taxonomy(STACKS)


def classify(job: Job) -> None:
    """Mutate `job` in place: assign industries / workflows / stacks / opp_score."""
    text = job.haystack
    job.industries = match_categories(text, INDUSTRIES_RE)
    job.workflows  = match_categories(text, WORKFLOWS_RE)
    job.stacks     = match_categories(text, STACKS_RE)
    job.opp_score  = opportunity_score(job)


def opportunity_score(job: Job) -> float:
    """0-100 ranking proxy: budget × verified × low-competition.

    Weights:
      - budget         50%
      - verified       20%
      - low competition 30%
    """
    if job.budget_type == "HOURLY":
        rate = (
            (job.budget_min + job.budget_max) / 2
            if (job.budget_min and job.budget_max)
            else job.budget_amount
        )
        budget_score = min(rate / 100, 1.0) if rate else 0.2
    elif job.budget_type == "FIXED" and job.budget_amount > 0:
        budget_score = min(job.budget_amount / 5000, 1.0)
    else:
        budget_score = 0.2

    verified_score = 1.0 if job.client_verified else 0.3
    props = job.total_applicants or 0
    competition_score = 1 / (1 + props / 15)

    return round(
        (budget_score * 0.50 + verified_score * 0.20 + competition_score * 0.30) * 100,
        1,
    )


def persist(jobs: list[Job]) -> None:
    """Write each job's regex classifications into the cache table."""
    rows: list[dict] = []
    for j in jobs:
        for label in j.industries:
            rows.append({"job_id": j.id, "axis": "industry", "label": label, "source": "regex"})
        for label in j.workflows:
            rows.append({"job_id": j.id, "axis": "workflow", "label": label, "source": "regex"})
        for label in j.stacks:
            rows.append({"job_id": j.id, "axis": "stack", "label": label, "source": "regex"})
    save_classifications(rows)
