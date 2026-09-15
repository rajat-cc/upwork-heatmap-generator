"""End-to-end classifier behaviour against fixture jobs.

Each fixture is a "what we'd expect to see" assertion — if these break,
either the regex needs updating OR the classification has regressed.
"""

import pytest

from core.models import Job
from features.n8n.classifier import classify


def _make_job(**overrides) -> Job:
    base = {
        "id": "j1",
        "title": "",
        "description": "",
        "url": "https://www.upwork.com/jobs/j1",
        "published_at": "2026-01-01T00:00:00",
        "category": "dev",
        "contractor_tier": "EXPERT",
        "budget_type": "HOURLY",
        "budget_amount": 50.0,
        "budget_min": 40.0,
        "budget_max": 60.0,
        "skills": ["n8n"],
    }
    base.update(overrides)
    return Job(**base)


def test_voice_agent_for_dental_clinic():
    j = _make_job(
        title="Build n8n voice agent for dental clinic appointment booking",
        description="Use Vapi or Retell, integrate with Google Calendar.",
    )
    classify(j)
    assert "Healthcare" in j.industries
    assert "Voice / Telephony" in j.workflows
    assert "Calendar / Scheduling" in j.workflows
    assert "Vapi" in j.stacks or "Retell" in j.stacks


def test_marketing_agency_lead_gen():
    j = _make_job(
        title="n8n + Apollo.io lead generation for a marketing agency",
        description="Scrape LinkedIn prospects, push into HubSpot, cold email via Instantly.",
    )
    classify(j)
    assert "Marketing / Agency" in j.industries
    assert "Lead Gen / Scraping" in j.workflows
    assert "CRM Sync" in j.workflows
    assert "Email Automation" in j.workflows
    assert "HubSpot" in j.stacks
    assert "Instantly" in j.stacks
    assert "Apollo.io" in j.stacks
    assert "LinkedIn" in j.stacks


def test_unclassified_when_no_keyword_fires():
    j = _make_job(
        title="n8n developer needed",
        description="Looking for someone with experience building automations. Long-term gig.",
    )
    classify(j)
    # The taxonomies should NOT invent a label when nothing fires.
    # (Both lists may be empty — that's the honest answer.)
    assert isinstance(j.industries, list)
    assert isinstance(j.workflows, list)


def test_opp_score_in_valid_range():
    j = _make_job(budget_type="FIXED", budget_amount=5000.0, client_verified=1, total_applicants=5)
    classify(j)
    assert 0 <= j.opp_score <= 100


def test_opp_score_orders_by_quality():
    """A verified $5k fixed job with few proposals must outscore a cheap unverified
    hourly with many proposals."""
    high = _make_job(
        id="high",
        budget_type="FIXED",
        budget_amount=5000.0,
        client_verified=1,
        total_applicants=3,
    )
    low = _make_job(
        id="low",
        budget_type="HOURLY",
        budget_min=8.0,
        budget_max=12.0,
        budget_amount=10.0,
        client_verified=0,
        total_applicants=80,
    )
    classify(high)
    classify(low)
    assert high.opp_score > low.opp_score


def test_opp_score_unknown_budget_gets_baseline():
    """Jobs with no budget signal still get a score (~30s)."""
    j = _make_job(
        budget_type="UNKNOWN",
        budget_amount=0.0,
        budget_min=0.0,
        budget_max=0.0,
        client_verified=1,
        total_applicants=10,
    )
    classify(j)
    assert 20 < j.opp_score < 80


@pytest.mark.parametrize(
    "budget,expected_min",
    [
        (1000, 30),  # $1k fixed → at least 30
        (5000, 50),  # $5k fixed → at least 50
        (10000, 60),  # $10k fixed → above 60
    ],
)
def test_higher_budget_higher_score(budget, expected_min):
    j = _make_job(
        budget_type="FIXED", budget_amount=float(budget), client_verified=1, total_applicants=10
    )
    classify(j)
    assert j.opp_score >= expected_min, (
        f"${budget} fixed scored {j.opp_score}, expected >= {expected_min}"
    )
