"""Classification + opportunity scoring for automation jobs.

Four regex axes: industry, workflow, stack, platform. Labels are persisted in
`job_classifications` so analysis stays possible after the 24 h text purge.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.models import Job
from db import save_classifications
from taxonomies.automation import PLATFORMS
from taxonomies.compile import compile_taxonomy, match_categories
from taxonomies.n8n import INDUSTRIES, STACKS, WORKFLOWS

INDUSTRIES_RE = compile_taxonomy(INDUSTRIES)
WORKFLOWS_RE = compile_taxonomy(WORKFLOWS)
STACKS_RE = compile_taxonomy(STACKS)
PLATFORMS_RE = compile_taxonomy(PLATFORMS)

_AXES = (
    ("industry", "industries", INDUSTRIES_RE),
    ("workflow", "workflows", WORKFLOWS_RE),
    ("stack", "stacks", STACKS_RE),
    ("platform", "platforms", PLATFORMS_RE),
)


def classify(job: Job) -> None:
    """Mutate `job` in place: assign industries / workflows / stacks / platforms / opp_score."""
    text = job.haystack
    for _axis, attr, compiled in _AXES:
        setattr(job, attr, match_categories(text, compiled))
    job.opp_score = opportunity_score(job)


def apply_cached(job: Job, cached: dict | None) -> None:
    """Assign labels from the cache (for rows whose text was purged)."""
    cached = cached or {}
    for axis, attr, _compiled in _AXES:
        setattr(job, attr, list(cached.get(axis, [])))
    job.opp_score = opportunity_score(job)


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


def rows_for(jobs: Iterable[Job], source: str = "regex") -> list[dict]:
    rows: list[dict] = []
    for j in jobs:
        for axis, attr, _compiled in _AXES:
            for label in getattr(j, attr):
                rows.append({"job_id": j.id, "axis": axis, "label": label, "source": source})
    return rows


def persist(jobs: Iterable[Job]) -> int:
    """Write each job's regex classifications into the cache table; returns row count."""
    rows = rows_for(jobs)
    save_classifications(rows)
    return len(rows)


def classify_rows(rows: Iterable) -> list[Job]:
    """sqlite rows → classified Jobs (skipping rows whose text is already purged)."""
    jobs: list[Job] = []
    for r in rows:
        job = Job.from_row(r)
        if job.is_purged:
            continue
        classify(job)
        jobs.append(job)
    return jobs
