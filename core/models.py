"""Typed domain models.

A senior-architect note: these are intentionally `slots=True` dataclasses
rather than Pydantic models — we don't need runtime validation here, we
need cheap structs that the type checker and IDE can reason about. The
DB layer still exchanges plain dicts at the SQL boundary; conversion
happens in `Job.from_row()` and `Job.to_db_dict()`.
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
        """Build a Job from a sqlite3.Row (or any mapping-like)."""
        skills_raw = row["skills"] if "skills" in row.keys() else "[]"
        try:
            skills = json.loads(skills_raw or "[]")
        except (ValueError, TypeError):
            skills = []
        return cls(
            id=row["id"],
            title=row["title"] or "",
            description=row["description"] or "",
            url=row["url"] if "url" in row.keys() and row["url"] else f"https://www.upwork.com/jobs/{row['id']}",
            published_at=row["published_at"] or "",
            category=row["category"] or "",
            contractor_tier=row["contractor_tier"] or "",
            budget_type=row["budget_type"] or "",
            budget_amount=float(row["budget_amount"] or 0),
            budget_min=float(row["budget_min"] or 0),
            budget_max=float(row["budget_max"] or 0),
            skills=skills,
            total_applicants=int(row["total_applicants"] or 0),
            client_total_hires=int(row["client_total_hires"] or 0),
            client_total_spent=float(row["client_total_spent"] or 0),
            client_verified=int(row["client_verified"] or 0),
            client_feedback=float(row["client_feedback"] or 0),
            client_country=row["client_country"] or "",
            is_premium=int(row["is_premium"] or 0) if "is_premium" in row.keys() else 0,
            is_enterprise=int(row["is_enterprise"] or 0) if "is_enterprise" in row.keys() else 0,
            duration_label=row["duration_label"] or "" if "duration_label" in row.keys() else "",
            first_seen_at=row["first_seen_at"] if "first_seen_at" in row.keys() and row["first_seen_at"] else "",
            last_fetched_at=row["last_fetched_at"] if "last_fetched_at" in row.keys() and row["last_fetched_at"] else "",
            fetch_count=int(row["fetch_count"] or 1) if "fetch_count" in row.keys() else 1,
            discovered_via_search=row["discovered_via_search"] if "discovered_via_search" in row.keys() and row["discovered_via_search"] else "",
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
    jobs: list[Job]                       # sorted by opp_score DESC
