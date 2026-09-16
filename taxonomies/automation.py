"""Automation platform taxonomy.

A fourth classification axis ("platform") alongside industry / workflow /
stack. It lets the automation lens count the whole market instead of only
posts that contain the literal word "n8n", which is what the population used
to be. Keywords are matched as whole words (see `taxonomies.compile`).
"""

from __future__ import annotations

PLATFORMS: list[tuple[str, list[str]]] = [
    ("n8n", ["n8n", "n8n.io", "n8n.cloud", "n8n workflow", "n8n workflows"]),
    ("Make", ["make.com", "integromat", "make scenario", "make scenarios", "make automation"]),
    ("Zapier", ["zapier", "zap", "zaps"]),
    ("GoHighLevel", ["gohighlevel", "ghl", "go high level", "highlevel"]),
    ("Apps Script", ["apps script", "appscript", "app script", "google apps script"]),
    ("Power Automate", ["power automate", "microsoft flow"]),
    ("Pipedream", ["pipedream"]),
    ("Airtable Automations", ["airtable automation", "airtable automations"]),
    (
        "Custom code / API",
        ["custom script", "python script", "cron job", "custom integration", "serverless function"],
    ),
]
