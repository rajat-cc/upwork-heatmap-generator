"""Typed domain models.

A senior-architect note: these are intentionally `slots=True` dataclasses
rather than Pydantic models — we don't need runtime validation here, we
need cheap structs that the type checker and IDE can reason about. The
DB layer still exchanges plain dicts at the SQL boundary; conversion
happens in `Job.from_row()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Job:
    """One Upwork job posting, hydrated from a SQLite row."""

    id: str
    title: str
    description: str
    url: str
    published_at: str
    category: str
    contractor_tier: str
    budget_type: str
    budget_amount: float
    budget_min: float
    budget_max: float
    skills: list[str] = field(default_factory=list)
    total_applicants: int = 0
    client_total_hires: int = 0
    client_total_spent: float = 0.0
    client_verified: int = 0
    client_feedback: float = 0.0
    client_country: str = ""
    is_premium: int = 0
    is_enterprise: int = 0
    duration_label: str = ""
    first_seen_at: str = ""
    last_fetched_at: str = ""
    fetch_count: int = 1
    discovered_via_search: str = ""

    # Tags applied by classifiers (mutated by `analyze`; not persisted on Job)
    industries: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    stacks: list[str] = field(default_factory=list)
    opp_score: float = 0.0

    @classmethod
    def from_row(cls, row: Any) -> Job:
        """Build a Job from a sqlite3.Row (or any mapping-like).

        The row is copied into a dict first: `sqlite3.Row` exposes `keys()`,
        but `"x" in row` tests *values*, so membership checks and `.get()`
        must go through a real dict. Columns added by later migrations may be
        absent on older rows and fall back to their defaults.
        """
        data = dict(row)
        try:
            skills = json.loads(data.get("skills") or "[]")
        except (ValueError, TypeError):
            skills = []
        job_id = data["id"]
        return cls(
            id=job_id,
            title=data.get("title") or "",
            description=data.get("description") or "",
            url=data.get("url") or f"https://www.upwork.com/jobs/{job_id}",
            published_at=data.get("published_at") or "",
            category=data.get("category") or "",
            contractor_tier=data.get("contractor_tier") or "",
            budget_type=data.get("budget_type") or "",
            budget_amount=float(data.get("budget_amount") or 0),
            budget_min=float(data.get("budget_min") or 0),
            budget_max=float(data.get("budget_max") or 0),
            skills=skills,
            total_applicants=int(data.get("total_applicants") or 0),
            client_total_hires=int(data.get("client_total_hires") or 0),
            client_total_spent=float(data.get("client_total_spent") or 0),
            client_verified=int(data.get("client_verified") or 0),
            client_feedback=float(data.get("client_feedback") or 0),
            client_country=data.get("client_country") or "",
            is_premium=int(data.get("is_premium") or 0),
            is_enterprise=int(data.get("is_enterprise") or 0),
            duration_label=data.get("duration_label") or "",
            first_seen_at=data.get("first_seen_at") or "",
            last_fetched_at=data.get("last_fetched_at") or "",
            fetch_count=int(data.get("fetch_count") or 1),
            discovered_via_search=data.get("discovered_via_search") or "",
        )

    @property
    def haystack(self) -> str:
        """Concatenated text used for keyword classification."""
        return f"{self.title} {self.description} {' '.join(self.skills)}"


@dataclass(slots=True)
class WorkflowStats:
    """Per-workflow money + competition signals."""

    med_hourly: float = 0.0
    med_fixed: float = 0.0
    avg_proposals: float = 0.0
    verified_pct: int = 0


@dataclass(slots=True)
class N8nReport:
    """Output of the n8n analyser, ready for renderer/exporter."""

    total_jobs: int
    industry_count: list[tuple[str, int]]
    workflow_count: list[tuple[str, int]]
    stack_count: list[tuple[str, int]]
    matrix: dict[str, dict[str, int]]
    workflow_stats: dict[str, WorkflowStats]
    industry_jobs: dict[str, list[Job]]
    workflow_jobs: dict[str, list[Job]]
    unclassified_industry: int
    unclassified_workflow: int
    jobs: list[Job]  # sorted by opp_score DESC
