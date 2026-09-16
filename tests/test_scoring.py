"""scoring.toml loads, the score is bounded and explainable, and every component moves it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.models import Job
from core.scoring import ScoreContext, Scoring, client_segment_of, explain_rows, score_job


def _job(**over) -> Job:
    base = {
        "id": "j",
        "title": "n8n HubSpot sync",
        "description": "",
        "url": "",
        "published_at": "",
        "category": "",
        "contractor_tier": "EXPERT",
        "budget_type": "HOURLY",
        "budget_amount": 50,
        "budget_min": 40,
        "budget_max": 60,
        "total_applicants": 10,
        "client_total_hires": 3,
        "client_total_spent": 2000,
        "client_verified": 1,
        "duration_label": "1 to 3 months",
    }
    base.update(over)
    return Job(**base)


def _component(score, name):
    return next(c for c in score.components if c.name == name)


def test_scoring_file_loads_and_weights_sum_to_one():
    s = Scoring.load()
    assert s.version and abs(sum(s.weights.values()) - 1.0) < 1e-9
    assert s.skills["demand_weight"] == 0.45
    assert ("Scoring version", s.version) in s.summary_rows()


def test_bad_weights_are_rejected(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('version = "x"\n[weights]\na = 0.5\nb = 0.4\n')
    with pytest.raises(ValueError, match="sum to 1.0"):
        Scoring.load(p)


def test_score_is_bounded_and_components_sum():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    job = _job(published_at="2026-09-16T11:00:00")
    score = score_job(job, ScoreContext(now=now))
    assert 0 <= score.total <= 100
    assert abs(sum(c.contribution for c in score.components) - score.total) < 0.11
    names = [c.name for c in score.components]
    assert names == [
        "win_probability",
        "expected_value",
        "hire_rate",
        "freshness",
        "competition",
        "fit",
    ]
    assert len(explain_rows(score)) == 6
    assert score.insufficient_data  # no funnel context → prior


def test_segment_priors_and_funnel_context():
    job = _job()
    assert client_segment_of(job) == "active"
    prior = score_job(job, ScoreContext())
    ctx = ScoreContext(win_by_segment={"active": (0.4, False)})
    informed = score_job(job, ctx)
    assert informed.total > prior.total and not informed.insufficient_data
    assert client_segment_of(_job(client_verified=0)) == "risky"
    assert client_segment_of(_job(client_total_spent=20000, client_total_hires=9)) == "champion"
    assert client_segment_of(_job(client_total_hires=0)) == "new"


def test_each_component_moves_the_score():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    fresh = _job(published_at="2026-09-16T11:00:00")
    stale = _job(published_at="2026-09-10T11:00:00")
    assert (
        score_job(fresh, ScoreContext(now=now)).total
        > score_job(stale, ScoreContext(now=now)).total
    )

    rich = _job(budget_min=90, budget_max=110)
    poor = _job(budget_min=15, budget_max=20)
    unknown = _job(budget_type="UNKNOWN", budget_amount=0, budget_min=0, budget_max=0)
    assert score_job(rich).total > score_job(poor).total > score_job(unknown).total

    crowded = _job(total_applicants=60)
    assert score_job(_job(total_applicants=2)).total > score_job(crowded).total

    good_client = _job(hire_rate=0.9, client_total_posted=10, client_total_hires=9)
    bad_client = _job(hire_rate=0.1, client_total_posted=10, client_total_hires=1)
    assert score_job(good_client).total > score_job(bad_client).total

    ctx = ScoreContext(portfolio_terms=frozenset({"hubspot", "n8n", "zapier"}))
    assert score_job(_job(), ctx).total > score_job(_job(title="Logo design"), ctx).total

    latest = ScoreContext(latest_applicants={"j": 80})
    assert (
        score_job(_job(total_applicants=2), latest).total
        < score_job(_job(total_applicants=2)).total
    )


def test_duration_factor_scales_expected_value():
    long = _job(duration_label="More than 6 months")
    short = _job(duration_label="Less than 1 month")
    assert (
        _component(score_job(long), "expected_value").normalized
        > _component(score_job(short), "expected_value").normalized
    )


def test_freshness_half_life():
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    day_old = _job(published_at=(now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"))
    fresh = _component(score_job(day_old, ScoreContext(now=now)), "freshness")
    assert abs(fresh.normalized - 0.5) < 1e-6
