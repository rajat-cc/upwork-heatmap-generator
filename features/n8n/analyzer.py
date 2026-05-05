"""Read jobs from the DB and aggregate into an N8nReport."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from core.logging_setup import get_logger
from core.models import Job, N8nReport, WorkflowStats
from db import get_conn, search_jobs_fts
from features.n8n.classifier import classify

log = get_logger(__name__)

# Strict word-boundary check used after the FTS5 prefilter.
_N8N_RE = re.compile(r"\bn8n\b", re.IGNORECASE)


def load_n8n_jobs(days: int = 7) -> list[Job]:
    """Two-stage filter:

    1. FTS5 `MATCH 'n8n'` against title+description+skills — fast index scan
    2. Strict `\\bn8n\\b` regex re-validation — kills "n8nx"/URL false positives
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    candidate_ids = search_jobs_fts("n8n", since_iso=cutoff)
    if not candidate_ids:
        log.info("No n8n candidates in the last %d days", days)
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})",
            candidate_ids,
        ).fetchall()

    jobs: list[Job] = []
    for r in rows:
        j = Job.from_row(r)
        if not _N8N_RE.search(j.haystack):
            continue
        jobs.append(j)
    log.info("Loaded %d n8n jobs (FTS5 candidates: %d)", len(jobs), len(candidate_ids))
    return jobs


def analyze(jobs: list[Job]) -> N8nReport:
    """Tag every job and aggregate."""
    industry_count: Counter[str] = Counter()
    workflow_count: Counter[str] = Counter()
    stack_count: Counter[str]    = Counter()
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    industry_jobs: dict[str, list[Job]] = defaultdict(list)
    workflow_jobs: dict[str, list[Job]] = defaultdict(list)

    unclassified_industry = 0
    unclassified_workflow = 0

    for job in jobs:
        classify(job)

        if not job.industries:
            unclassified_industry += 1
        if not job.workflows:
            unclassified_workflow += 1

        for ind in job.industries:
            industry_count[ind] += 1
            industry_jobs[ind].append(job)
        for wf in job.workflows:
            workflow_count[wf] += 1
            workflow_jobs[wf].append(job)
        for st in job.stacks:
            stack_count[st] += 1

        for ind in job.industries:
            for wf in job.workflows:
                matrix[ind][wf] += 1

    workflow_stats = {wf: _money_stats(js) for wf, js in workflow_jobs.items()}

    return N8nReport(
        total_jobs=len(jobs),
        industry_count=industry_count.most_common(),
        workflow_count=workflow_count.most_common(),
        stack_count=stack_count.most_common(),
        matrix={k: dict(v) for k, v in matrix.items()},
        workflow_stats=workflow_stats,
        industry_jobs=dict(industry_jobs),
        workflow_jobs=dict(workflow_jobs),
        unclassified_industry=unclassified_industry,
        unclassified_workflow=unclassified_workflow,
        jobs=sorted(jobs, key=lambda j: j.opp_score, reverse=True),
    )


def _money_stats(jobs: list[Job]) -> WorkflowStats:
    hourly: list[float] = []
    fixed: list[float] = []
    proposals: list[int] = []
    verified = 0

    for j in jobs:
        if j.budget_type == "HOURLY":
            mid = (
                (j.budget_min + j.budget_max) / 2
                if (j.budget_min and j.budget_max)
                else j.budget_amount or 0
            )
            if mid > 0:
                hourly.append(mid)
        elif j.budget_type == "FIXED" and j.budget_amount > 0:
            fixed.append(j.budget_amount)
        if j.total_applicants > 0:
            proposals.append(j.total_applicants)
        if j.client_verified:
            verified += 1

    return WorkflowStats(
        med_hourly=round(median(hourly), 1) if hourly else 0,
        med_fixed=round(median(fixed), 0) if fixed else 0,
        avg_proposals=sum(proposals) / len(proposals) if proposals else 0,
        verified_pct=round(verified / len(jobs) * 100) if jobs else 0,
    )
