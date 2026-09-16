"""Read jobs from the DB and aggregate into an N8nReport.

Cache-first: the population is the union of jobs whose remaining text
matches `n8n` and jobs whose cached `platform` label is `n8n`. Rows whose
text was purged by the retention job are analysed from their cached labels,
so the 24 h text limit does not shrink the analysis window.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from core.logging_setup import get_logger
from core.models import Job, N8nReport, WorkflowStats
from core.stats import band, median
from db import get_jobs_by_ids, job_ids_with_label, load_classifications, search_jobs_fts
from features.n8n.classifier import apply_cached, classify

log = get_logger(__name__)

# Strict word-boundary check used after the FTS5 prefilter.
_N8N_RE = re.compile(r"\bn8n\b", re.IGNORECASE)


def load_n8n_jobs(days: int = 7) -> list[Job]:
    """Three-stage filter:

    1. FTS5 `MATCH 'n8n'` against title+description+skills — fast index scan
    2. cached `platform = n8n` labels — covers rows whose text was purged
    3. strict `\\bn8n\\b` regex re-validation on rows that still have text
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    fts_ids = set(search_jobs_fts("n8n", since_iso=cutoff))
    cached_ids = set(job_ids_with_label("platform", "n8n", since_iso=cutoff))
    candidate_ids = fts_ids | cached_ids
    if not candidate_ids:
        log.info("No n8n candidates in the last %d days", days)
        return []

    jobs: list[Job] = []
    for r in get_jobs_by_ids(sorted(candidate_ids)):
        j = Job.from_row(r)
        if j.is_purged:
            if j.id in cached_ids:
                jobs.append(j)
            continue
        if _N8N_RE.search(j.haystack):
            jobs.append(j)
    log.info(
        "Loaded %d n8n jobs (FTS candidates: %d, cached: %d)",
        len(jobs),
        len(fts_ids),
        len(cached_ids),
    )
    return jobs


def analyze(jobs: list[Job]) -> N8nReport:
    """Tag every job (live regex, or cached labels when the text is gone) and aggregate."""
    industry_count: Counter[str] = Counter()
    workflow_count: Counter[str] = Counter()
    stack_count: Counter[str] = Counter()
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    industry_jobs: dict[str, list[Job]] = defaultdict(list)
    workflow_jobs: dict[str, list[Job]] = defaultdict(list)

    unclassified_industry = 0
    unclassified_workflow = 0

    purged_ids = [j.id for j in jobs if j.is_purged]
    cached = load_classifications(purged_ids) if purged_ids else {}

    for job in jobs:
        if job.is_purged:
            apply_cached(job, cached.get(job.id))
        else:
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

    h25, h50, h75 = band(hourly)
    f25, f50, f75 = band(fixed)
    return WorkflowStats(
        med_hourly=round(h50, 1),
        med_fixed=round(f50, 0),
        med_proposals=median(proposals),
        verified_pct=round(verified / len(jobs) * 100) if jobs else 0,
        hourly_p25=round(h25, 1),
        hourly_p75=round(h75, 1),
        fixed_p25=round(f25, 0),
        fixed_p75=round(f75, 0),
        n_hourly=len(hourly),
        n_fixed=len(fixed),
    )
