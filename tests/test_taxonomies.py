"""Sanity checks on the taxonomy data and matching engine.

These catch the kind of bug that silently shifts every recommendation:
  - a regex that no longer compiles
  - a substring-style false positive snuck in
  - a label with an empty keyword list (would match nothing)
"""

import pytest

from taxonomies.compile import compile_taxonomy, match_categories
from taxonomies.n8n import (
    INDUSTRIES,
    STACKS,
    WORKFLOW_ABBR,
    WORKFLOWS,
    abbr_workflow,
)


@pytest.mark.parametrize(
    "taxonomy,name",
    [
        (INDUSTRIES, "INDUSTRIES"),
        (WORKFLOWS, "WORKFLOWS"),
        (STACKS, "STACKS"),
    ],
)
def test_every_label_has_at_least_one_keyword(taxonomy, name):
    for label, kws in taxonomy:
        assert kws, f"{name}.{label!r} has no keywords"
        assert all(isinstance(k, str) and k for k in kws), (
            f"{name}.{label!r} has empty/non-str keyword"
        )


@pytest.mark.parametrize(
    "taxonomy,name",
    [
        (INDUSTRIES, "INDUSTRIES"),
        (WORKFLOWS, "WORKFLOWS"),
        (STACKS, "STACKS"),
    ],
)
def test_every_taxonomy_compiles_without_error(taxonomy, name):
    compiled = compile_taxonomy(taxonomy)
    assert len(compiled) == len(taxonomy), f"{name}: lost a category in compile"


@pytest.mark.parametrize(
    "taxonomy,name",
    [
        (INDUSTRIES, "INDUSTRIES"),
        (WORKFLOWS, "WORKFLOWS"),
        (STACKS, "STACKS"),
    ],
)
def test_labels_are_unique_per_axis(taxonomy, name):
    labels = [label for label, _ in taxonomy]
    assert len(labels) == len(set(labels)), (
        f"{name} has duplicate labels: {[lab for lab in labels if labels.count(lab) > 1]}"
    )


def test_word_boundary_prevents_substring_false_positives():
    """`ai` must not match inside `main`, `email`, etc."""
    compiled = compile_taxonomy([("AI thing", ["ai"])])
    assert match_categories("AI agent for sales", compiled) == ["AI thing"]
    assert match_categories("main thread for email", compiled) == []


def test_phrases_with_dots_match_correctly():
    """`apollo.io` should match the literal token, not be treated as regex."""
    compiled = compile_taxonomy([("Apollo", ["apollo.io"])])
    assert match_categories("scraping with apollo.io", compiled) == ["Apollo"]
    assert match_categories("apollo io is similar", compiled) == []


def test_n8n_classification_realistic_job():
    """End-to-end smoke through the real INDUSTRIES + WORKFLOWS + STACKS tables."""
    text = (
        "n8n automation specialist for a property management agency — "
        "sync Zillow listings to HubSpot CRM and send Slack notifications. "
        "Will use OpenAI GPT-4 for lead scoring."
    )
    industries = match_categories(text, compile_taxonomy(INDUSTRIES))
    workflows = match_categories(text, compile_taxonomy(WORKFLOWS))
    stacks = match_categories(text, compile_taxonomy(STACKS))

    assert "Real Estate" in industries  # property management, zillow
    assert "CRM Sync" in workflows  # crm, hubspot
    assert "Slack / Discord / Chat" in workflows
    assert "AI / LLM Agents" in workflows  # openai, gpt-4
    assert "HubSpot" in stacks
    assert "Slack" in stacks
    assert "OpenAI / GPT" in stacks


def test_workflow_abbr_covers_every_workflow():
    """The Industry × Workflow heatmap relies on every label having an abbr."""
    workflow_labels = {label for label, _ in WORKFLOWS}
    missing = workflow_labels - set(WORKFLOW_ABBR.keys())
    assert not missing, f"Missing abbreviations for workflows: {missing}"


def test_abbr_workflow_falls_back_for_unknown():
    assert abbr_workflow("Some Unknown Workflow") == "Some U"  # first 6 chars
