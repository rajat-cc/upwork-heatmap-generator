"""Snapshot test for `_parse_job` against a saved GraphQL response.

If Upwork ever changes their schema, this test breaks loudly — we'd
rather know on the test than discover it via silently NULL DB columns.
"""

from fetcher import _parse_job

# Minimised version of a real GraphQL response node.
SAMPLE_NODE = {
    "id": "~01abc",
    "title": "Build n8n automation for HubSpot lead routing",
    "description": "Need an automation that routes leads from HubSpot to Slack.",
    "publishedDateTime": "2026-04-15T12:34:56+0000",
    "experienceLevel": "EXPERT",
    "category": "Web, Mobile & Software Dev",
    "durationLabel": "Less than 1 month",
    "premium": False,
    "enterprise": False,
    "totalApplicants": 12,
    "skills": [{"name": "n8n"}, {"name": "hubspot"}, {"name": "automation"}],
    "amount": {"rawValue": None},
    "hourlyBudgetMin": {"rawValue": "40"},
    "hourlyBudgetMax": {"rawValue": "80"},
    "client": {
        "totalHires": 7,
        "totalPostedJobs": 15,
        "totalSpent": {"rawValue": "12500"},
        "verificationStatus": "VERIFIED",
        "totalFeedback": 4.9,
        "location": {"country": "United States"},
    },
}


def test_parse_node_returns_expected_dict():
    out = _parse_job(SAMPLE_NODE)

    assert out["id"] == "~01abc"
    assert out["title"].startswith("Build n8n")
    assert out["description"].startswith("Need an automation")
    # Date should have +0000 suffix stripped:
    assert out["published_at"] == "2026-04-15T12:34:56"
    assert out["budget_type"] == "HOURLY"
    assert out["budget_min"] == 40.0
    assert out["budget_max"] == 80.0
    assert out["budget_amount"] == 60.0  # midpoint
    assert out["client_verified"] == 1
    assert out["client_country"] == "United States"
    assert out["client_total_hires"] == 7
    assert out["client_total_spent"] == 12500.0
    assert out["category"] == "Web, Mobile & Software Dev"
    assert out["contractor_tier"] == "EXPERT"


def test_parse_handles_fixed_budget():
    node = {
        **SAMPLE_NODE,
        "amount": {"rawValue": "2500"},
        "hourlyBudgetMin": {"rawValue": None},
        "hourlyBudgetMax": {"rawValue": None},
    }
    out = _parse_job(node)
    assert out["budget_type"] == "FIXED"
    assert out["budget_amount"] == 2500.0


def test_parse_handles_no_budget():
    node = {
        **SAMPLE_NODE,
        "amount": {"rawValue": None},
        "hourlyBudgetMin": {"rawValue": None},
        "hourlyBudgetMax": {"rawValue": None},
    }
    out = _parse_job(node)
    assert out["budget_type"] == "UNKNOWN"
    assert out["budget_amount"] == 0.0


def test_parse_handles_missing_client():
    node = {**SAMPLE_NODE, "client": None}
    out = _parse_job(node)
    assert out["client_verified"] == 0
    assert out["client_country"] == ""
    assert out["client_total_hires"] == 0
