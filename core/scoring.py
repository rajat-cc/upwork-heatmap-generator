"""Personal opportunity score.

Every constant comes from `scoring.toml`; every component is a row that
`main.py explain <job_id>` prints, so a score is never a number you have to
trust. The context carries what only your own history can supply: the shrunk
win rate per client segment (from the funnel) and your portfolio terms.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from config import ROOT_DIR
from core.models import Job
from db import parse_iso

DEFAULT_PATH = Path(ROOT_DIR) / "scoring.toml"
_WORD = re.compile(r"[a-z0-9][a-z0-9\.\+#-]{1,}")


@dataclass(slots=True)
class Scoring:
    version: str
    weights: dict[str, float]
    normalizers: dict
    segment_priors: dict[str, float]
    thresholds: dict
    skills: dict
    caps: dict
    connects: dict
    path: str

    @classmethod
    def load(cls, path: str | Path | None = None) -> Scoring:
        p = Path(path or os.getenv("UPWORK_SCORING_FILE") or DEFAULT_PATH)
        with open(p, "rb") as f:
            doc = tomllib.load(f)
        weights = {k: float(v) for k, v in doc["weights"].items()}
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"{p}: [weights] must sum to 1.0, got {total:.4f}")
        return cls(
            version=str(doc.get("version", "unversioned")),
            weights=weights,
            normalizers=doc.get("normalizers", {}),
            segment_priors={k: float(v) for k, v in doc.get("segment_priors", {}).items()},
            thresholds=doc.get("thresholds", {}),
            skills=doc.get("skills", {}),
            caps=doc.get("caps", {}),
            connects=doc.get("connects", {}),
            path=str(p),
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        """(name, value) pairs for headers and Excel summaries."""
        rows = [("Scoring version", self.version)]
        rows += [(f"weight {k}", f"{v:.2f}") for k, v in self.weights.items()]
        rows += [
            ("hourly cap $/hr", str(self.normalizers.get("hourly_rate_cap"))),
            ("fixed cap $", str(self.normalizers.get("fixed_amount_cap"))),
            ("competition scale", str(self.normalizers.get("competition_scale"))),
            ("freshness half-life h", str(self.normalizers.get("freshness_half_life_hours"))),
        ]
        return rows


_scoring: Scoring | None = None


def get_scoring() -> Scoring:
    global _scoring
    if _scoring is None:
        _scoring = Scoring.load()
    return _scoring


@dataclass(slots=True)
class Component:
    name: str
    raw: str
    normalized: float
    weight: float
    note: str = ""

    @property
    def contribution(self) -> float:
        return self.normalized * self.weight * 100


@dataclass(slots=True)
class Score:
    total: float
    components: list[Component]
    insufficient_data: bool
    version: str
    segment: str


@dataclass(slots=True)
class ScoreContext:
    """What only your own history can supply."""

    win_by_segment: dict[str, tuple[float, bool]] = field(
        default_factory=dict
    )  # seg → (shrunk, insufficient)
    portfolio_terms: frozenset[str] = frozenset()
    latest_applicants: dict[str, int] = field(default_factory=dict)
    now: datetime | None = None


def client_segment_of(job: Job, scoring: Scoring | None = None) -> str:
    s = scoring or get_scoring()
    if not job.client_verified:
        return "risky"
    if job.client_total_spent >= s.thresholds.get(
        "champion_spend", 10000
    ) and job.client_total_hires >= s.thresholds.get("champion_hires", 5):
        return "champion"
    return "active" if job.client_total_hires >= 1 else "new"


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def load_portfolio_terms(path: str | None = None) -> frozenset[str]:
    p = path or os.getenv("UPWORK_PORTFOLIO_TAGS", "")
    if not p or not Path(p).is_file():
        return frozenset()
    terms = {line.strip().lower() for line in Path(p).read_text().splitlines()}
    return frozenset(t for t in terms if t and not t.startswith("#"))


def score_job(job: Job, ctx: ScoreContext | None = None, scoring: Scoring | None = None) -> Score:
    s = scoring or get_scoring()
    ctx = ctx or ScoreContext()
    n = s.normalizers
    now = ctx.now or datetime.now(UTC)
    comps: list[Component] = []

    # 1. win probability from your funnel, by client segment
    segment = client_segment_of(job, s)
    if segment in ctx.win_by_segment:
        win, insufficient = ctx.win_by_segment[segment]
        note = "your funnel" + (" (n<5, shrunk)" if insufficient else "")
    else:
        win, insufficient = s.segment_priors.get(segment, 0.1), True
        note = "prior (no funnel data for this segment)"
    cap = float(n.get("win_probability_cap", 0.5))
    comps.append(
        Component(
            "win_probability",
            f"{win:.0%} · {segment}",
            min(win / cap, 1.0),
            s.weights["win_probability"],
            note,
        )
    )

    # 2. expected value: budget vs cap × duration factor
    if job.budget_type == "HOURLY":
        rate = (
            (job.budget_min + job.budget_max) / 2
            if (job.budget_min and job.budget_max)
            else job.budget_amount
        )
        base = (
            min(rate / float(n.get("hourly_rate_cap", 100.0)), 1.0)
            if rate
            else float(n.get("unknown_budget_score", 0.2))
        )
        raw = f"${rate:.0f}/hr" if rate else "hourly, unspecified"
    elif job.budget_type == "FIXED" and job.budget_amount > 0:
        base = min(job.budget_amount / float(n.get("fixed_amount_cap", 5000.0)), 1.0)
        raw = f"${job.budget_amount:,.0f} fixed"
    else:
        base = float(n.get("unknown_budget_score", 0.2))
        raw = "budget unknown"
    factor = float(n.get("duration_factor", {}).get(job.duration_label or "", 1.0))
    comps.append(
        Component(
            "expected_value",
            f"{raw} × {factor:g} ({job.duration_label or 'no duration'})",
            min(base * factor, 1.0),
            s.weights["expected_value"],
        )
    )

    # 3. hire rate
    if job.hire_rate is None:
        comps.append(
            Component(
                "hire_rate",
                "unknown",
                float(n.get("neutral_hire_rate", 0.5)),
                s.weights["hire_rate"],
                "no posted-jobs count on this row",
            )
        )
    else:
        comps.append(
            Component(
                "hire_rate",
                f"{job.hire_rate:.0%} ({job.client_total_hires}/{job.client_total_posted})",
                min(job.hire_rate, 1.0),
                s.weights["hire_rate"],
            )
        )

    # 4. freshness
    published = parse_iso(job.published_at)
    if published is None:
        comps.append(
            Component("freshness", "unknown", 0.0, s.weights["freshness"], "no publish time")
        )
    else:
        hours = max(0.0, (now - published).total_seconds() / 3600)
        half = float(n.get("freshness_half_life_hours", 24.0))
        comps.append(
            Component(
                "freshness",
                f"{hours:.0f} h since publish",
                0.5 ** (hours / half),
                s.weights["freshness"],
            )
        )

    # 5. competition
    applicants = ctx.latest_applicants.get(job.id, job.total_applicants or 0)
    scale = float(n.get("competition_scale", 15.0))
    comps.append(
        Component(
            "competition",
            f"{applicants} applicants",
            1 / (1 + applicants / scale),
            s.weights["competition"],
        )
    )

    # 6. portfolio fit
    if ctx.portfolio_terms:
        words = _tokens(job.haystack)
        hits = sorted(
            t for t in ctx.portfolio_terms if t in words or (" " in t and t in job.haystack.lower())
        )
        fit = min(len(hits) / min(len(ctx.portfolio_terms), 10), 1.0)
        comps.append(Component("fit", ", ".join(hits[:5]) or "no overlap", fit, s.weights["fit"]))
    else:
        comps.append(
            Component(
                "fit",
                "no portfolio file",
                float(n.get("neutral_fit", 0.5)),
                s.weights["fit"],
                "set UPWORK_PORTFOLIO_TAGS",
            )
        )

    total = round(sum(c.contribution for c in comps), 1)
    return Score(
        total=max(0.0, min(100.0, total)),
        components=comps,
        insufficient_data=insufficient,
        version=s.version,
        segment=segment,
    )


def build_context(days: int = 90) -> ScoreContext:
    """Context from your own data: funnel win rates by segment, portfolio terms."""
    from features.funnel.analyzer import analyze  # lazy: features import core, not the reverse

    report = analyze(days)
    win = {
        row.value: (row.win.shrunk, row.win.insufficient)
        for row in report.segments.get("client_segment", [])
        if row.submitted
    }
    return ScoreContext(win_by_segment=win, portfolio_terms=load_portfolio_terms())


def explain_rows(score: Score) -> list[tuple[str, str, str, str, str]]:
    """Rows for the explain table: component, input, normalized, weight, contribution."""
    return [
        (c.name, c.raw, f"{c.normalized:.2f}", f"{c.weight:.2f}", f"{c.contribution:+.1f}")
        for c in score.components
    ]


def freshness_hours(job: Job, now: datetime | None = None) -> float | None:
    published = parse_iso(job.published_at)
    if published is None:
        return None
    return max(0.0, ((now or datetime.now(UTC)) - published).total_seconds() / 3600)


def is_finite(x: float) -> bool:
    return math.isfinite(x)
