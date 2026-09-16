"""Automation demand: the n8n analysis generalised to every platform label.

The population is every job carrying a `platform` label (cache-first, so
purged rows count), optionally narrowed to some platforms. On top of the
n8n report it adds a platform table and a platform × workflow matrix.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.logging_setup import get_logger
from core.models import Job, N8nReport
from db import get_jobs_by_ids, job_ids_with_label, search_jobs_fts
from features.n8n.analyzer import analyze as analyze_base
from features.n8n.classifier import classify
from taxonomies.automation import PLATFORMS

log = get_logger(__name__)

PLATFORM_LABELS = [name for name, _ in PLATFORMS]
_FTS_TERMS = {name: kws[0] for name, kws in PLATFORMS}


@dataclass(slots=True)
class AutomationReport:
    base: N8nReport
    platforms: list[str]  # the filter that produced this report (all when empty)
    platform_count: list[tuple[str, int]]
    platform_matrix: dict[str, dict[str, int]]  # platform → workflow → jobs

    @property
    def total_jobs(self) -> int:
        return self.base.total_jobs

    @property
    def jobs(self) -> list[Job]:
        return self.base.jobs


def _fts_candidates(term: str, cutoff: str) -> set[str]:
    """FTS prefilter for rows not yet classified; tolerant of odd terms like make.com."""
    try:
        return set(search_jobs_fts(f'"{term}"', since_iso=cutoff))
    except Exception as exc:  # FTS syntax errors on unusual terms must not abort
        log.debug("FTS candidate lookup skipped for %r: %s", term, exc)
        return set()


def load_jobs(days: int = 7, platforms: list[str] | None = None) -> list[Job]:
    wanted = [p for p in (platforms or PLATFORM_LABELS) if p in PLATFORM_LABELS]
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    cached_ids: set[str] = set()
    for p in wanted:
        cached_ids |= set(job_ids_with_label("platform", p, since_iso=cutoff))
    fts_ids: set[str] = set()
    for p in wanted:
        fts_ids |= _fts_candidates(_FTS_TERMS[p], cutoff)

    jobs: list[Job] = []
    for r in get_jobs_by_ids(sorted(cached_ids | fts_ids)):
        job = Job.from_row(r)
        if job.is_purged:
            if job.id in cached_ids:
                jobs.append(job)
            continue
        classify(job)
        if set(job.platforms) & set(wanted):
            jobs.append(job)
    log.info("Loaded %d automation jobs for %s", len(jobs), ", ".join(wanted))
    return jobs


def analyze(jobs: list[Job], platforms: list[str] | None = None) -> AutomationReport:
    base = analyze_base(jobs)  # classifies live rows, applies cache to purged ones
    platform_count: Counter[str] = Counter()
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for job in base.jobs:
        for p in job.platforms:
            platform_count[p] += 1
            for wf in job.workflows:
                matrix[p][wf] += 1
    return AutomationReport(
        base=base,
        platforms=list(platforms or []),
        platform_count=platform_count.most_common(),
        platform_matrix={k: dict(v) for k, v in matrix.items()},
    )
