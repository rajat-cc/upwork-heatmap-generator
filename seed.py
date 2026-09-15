"""Synthetic demo data — lets the whole pipeline run without an API key.

Every generated row carries the columns the fetcher would populate
(description text, client history, applicants, country, duration), so the
classifiers, FTS index, client segments and exports all have material.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime, timedelta

from config import CATEGORIES, CONTRACTOR_TIERS
from db import upsert_jobs

# Realistic demand weights — higher = appears more often in job postings
_WEIGHTS = {
    "Python": 10,
    "JavaScript": 10,
    "React": 9,
    "TypeScript": 7,
    "Node.js": 8,
    "PHP": 6,
    "WordPress": 7,
    "Django": 5,
    "FastAPI": 4,
    "Vue.js": 5,
    "Angular": 4,
    "AWS": 6,
    "Docker": 5,
    "Machine Learning": 6,
    "OpenAI": 7,
    "LLM": 6,
    "LangChain": 5,
    "RAG": 4,
    "PostgreSQL": 5,
    "MySQL": 5,
    "MongoDB": 4,
    "SQL": 6,
    "React Native": 5,
    "Flutter": 4,
    "Java": 5,
    "Spring Boot": 3,
    "Go": 3,
    "Rust": 2,
    "C#": 3,
    "C++": 2,
    "Data Analysis": 5,
    "Pandas": 4,
    "Power BI": 2,
    "Tableau": 2,
    "REST API": 6,
    "GraphQL": 3,
    "Microservices": 4,
    "DevOps": 4,
    "CI/CD": 3,
    "Kubernetes": 3,
    "Terraform": 2,
    "Linux": 4,
    "Web Scraping": 4,
    "Selenium": 3,
    "Playwright": 3,
    "iOS": 3,
    "Android": 3,
    "Swift": 2,
    "Kotlin": 2,
    "Shopify": 4,
    "Webflow": 3,
    "WooCommerce": 3,
    "Solidity": 2,
    "Web3": 2,
    "Blockchain": 2,
    "Computer Vision": 3,
    "NLP": 3,
    "Deep Learning": 4,
    "TensorFlow": 3,
    "PyTorch": 3,
    "Azure": 3,
    "GCP": 3,
    "Elasticsearch": 2,
    "Redis": 2,
    "Supabase": 3,
    "n8n": 5,
    "Zapier": 4,
    "Make.com": 3,
    "HubSpot": 3,
    "Airtable": 3,
}

_SKILL_LIST = list(_WEIGHTS.keys())
_SKILL_PROBS_RAW = [_WEIGHTS.get(s, 1) for s in _SKILL_LIST]
_TOTAL = sum(_SKILL_PROBS_RAW)
_SKILL_PROBS = [p / _TOTAL for p in _SKILL_PROBS_RAW]

# Category weights — more tech/dev jobs proportionally
_CAT_WEIGHTS = [40, 15, 20, 5, 10, 5, 5]

# Hourly posting pattern in UTC — mirrors US business hours (primary client base)
# 9am EST = 14:00 UTC, 9am PST = 17:00 UTC
_HOUR_WEIGHTS = [
    3, 2, 2, 2, 3, 4,  # 00-05 UTC
    6, 9, 12, 16, 20,  # 06-10 UTC
    24, 28, 30, 28, 25,  # 11-15 UTC  ← peak (9am–noon US East)
    22, 18, 15, 13, 11,  # 16-20 UTC
    9, 7, 5,  # 21-23 UTC
]  # fmt: skip

# Day-of-week weights (Mon=0 ... Sun=6)
_DAY_WEIGHTS = [22, 22, 20, 18, 12, 4, 2]

_JOBS_PER_DAY_BASE = 280

# Country names as the API returns them today (normalised to ISO in Phase 1).
_COUNTRIES = [
    ("United States", 45), ("United Kingdom", 10), ("Canada", 8), ("Australia", 6),
    ("India", 8), ("Germany", 4), ("United Arab Emirates", 4), ("Pakistan", 3),
    ("Nigeria", 2), ("France", 2), ("Netherlands", 2), ("Singapore", 2),
    ("Israel", 2), ("Spain", 2),
]  # fmt: skip
_COUNTRY_NAMES = [c for c, _ in _COUNTRIES]
_COUNTRY_WEIGHTS = [w for _, w in _COUNTRIES]

_VERBS = ["Build", "Automate", "Integrate", "Migrate", "Fix", "Design", "Scale", "Set up"]
_NOUNS = [
    "pipeline", "dashboard", "workflow", "API integration", "chatbot",
    "backend", "data sync", "scraper", "CRM automation", "reporting system",
]  # fmt: skip
_BIZ = [
    "e-commerce store", "marketing agency", "real estate team", "SaaS product",
    "healthcare clinic", "logistics company", "online course platform", "law firm",
    "recruiting firm", "podcast network",
]  # fmt: skip
_DURATIONS = ["Less than 1 month", "1 to 3 months", "3 to 6 months", "More than 6 months"]
_DURATION_WEIGHTS = [30, 45, 12, 13]

_DESC_TEMPLATES = [
    "We need an experienced {skill} developer to {verb} a {noun} for our {biz}. "
    "You must have shipped similar work before and be comfortable working async.",
    "Our {biz} is looking for help to {verb} a {noun} using {skill} and {skill2}. "
    "Please share two relevant examples and your availability this month.",
    "{verb} a {noun} that connects our tools ({skill}, {skill2}) for a growing {biz}. "
    "Clear documentation and a handover call are part of the scope.",
    "Looking for a {skill} specialist to {verb} a {noun}. The {biz} has an existing "
    "setup; we need it made reliable, documented, and easy to extend.",
]


def generate_demo_data(days: int = 14) -> int:
    now = datetime.now(UTC)
    jobs = []

    for day_offset in range(days):
        base_date = now - timedelta(days=day_offset)
        weekday = base_date.weekday()
        day_scale = _DAY_WEIGHTS[weekday] / max(_DAY_WEIGHTS)

        # Add week-over-week trend: older weeks slightly less volume
        week_factor = 1.0 - (day_offset / days) * 0.15
        daily_count = int(_JOBS_PER_DAY_BASE * day_scale * week_factor * random.uniform(0.88, 1.12))

        for _ in range(daily_count):
            hour = random.choices(range(24), weights=_HOUR_WEIGHTS, k=1)[0]
            pub_at = base_date.replace(
                hour=hour,
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
                microsecond=0,
            )

            category = random.choices(CATEGORIES, weights=_CAT_WEIGHTS, k=1)[0]
            tier = random.choices(CONTRACTOR_TIERS, weights=[20, 45, 35], k=1)[0]

            skill_count = random.choices([1, 2, 3, 4, 5, 6], weights=[5, 15, 30, 28, 15, 7], k=1)[0]
            skills = list(
                set(random.choices(_SKILL_LIST, weights=_SKILL_PROBS, k=skill_count * 2))
            )[:skill_count]

            budget_type, budget_amount = _random_budget()
            if budget_type == "HOURLY":
                budget_min = round(budget_amount * random.uniform(0.6, 0.9), 2)
                budget_max = round(budget_amount * random.uniform(1.1, 1.5), 2)
            else:
                budget_min = budget_max = round(budget_amount, 2)

            job_id = str(uuid.uuid4())
            verb = random.choice(_VERBS)
            noun = random.choice(_NOUNS)
            primary = skills[0] if skills else "Python"
            secondary = skills[1] if len(skills) > 1 else "REST API"
            client_hires, client_spent = _random_client_history()

            jobs.append(
                {
                    "id": job_id,
                    "title": f"{verb} {primary} {noun}",
                    "description": random.choice(_DESC_TEMPLATES).format(
                        skill=primary,
                        skill2=secondary,
                        verb=verb.lower(),
                        noun=noun,
                        biz=random.choice(_BIZ),
                    ),
                    "url": f"https://www.upwork.com/jobs/{job_id}",
                    "published_at": pub_at.strftime("%Y-%m-%dT%H:%M:%S"),
                    "category": category,
                    "contractor_tier": tier,
                    "budget_type": budget_type,
                    "budget_amount": round(budget_amount, 2),
                    "budget_min": budget_min,
                    "budget_max": budget_max,
                    "skills": json.dumps(skills),
                    "total_applicants": _random_applicants(),
                    "client_total_hires": client_hires,
                    "client_total_spent": client_spent,
                    "client_verified": 1 if random.random() < 0.82 else 0,
                    "client_feedback": round(random.uniform(4.0, 5.0), 2) if client_hires else 0.0,
                    "client_country": random.choices(_COUNTRY_NAMES, weights=_COUNTRY_WEIGHTS, k=1)[
                        0
                    ],
                    "is_premium": 1 if random.random() < 0.04 else 0,
                    "is_enterprise": 0,
                    "duration_label": random.choices(_DURATIONS, weights=_DURATION_WEIGHTS, k=1)[0],
                }
            )

    upsert_jobs(jobs, search_term="seed")
    return len(jobs)


def _random_budget() -> tuple:
    if random.random() < 0.60:
        roll = random.random()
        if roll < 0.40:
            amount = random.uniform(100, 500)
        elif roll < 0.75:
            amount = random.uniform(500, 1_500)
        elif roll < 0.93:
            amount = random.uniform(1_500, 5_000)
        else:
            amount = random.uniform(5_000, 15_000)
        return "FIXED", amount
    else:
        roll = random.random()
        if roll < 0.30:
            amount = random.uniform(10, 25)
        elif roll < 0.65:
            amount = random.uniform(25, 50)
        elif roll < 0.85:
            amount = random.uniform(50, 100)
        else:
            amount = random.uniform(100, 150)
        return "HOURLY", amount


def _random_applicants() -> int:
    """Skewed like the real market: most posts sit at 5–50, a long tail above."""
    roll = random.random()
    if roll < 0.06:
        return random.randint(0, 4)
    if roll < 0.32:
        return random.randint(5, 14)
    if roll < 0.82:
        return random.randint(15, 49)
    return random.randint(50, 120)


def _random_client_history() -> tuple[int, float]:
    """Hires and spend, correlated: a third of clients have never hired."""
    if random.random() < 0.33:
        return 0, 0.0
    hires = random.choices([1, 2, 4, 8, 15, 40], weights=[30, 22, 18, 15, 10, 5], k=1)[0]
    spent = round(hires * random.uniform(300, 4_000), 2)
    return hires, spent
