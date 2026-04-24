import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from config import CATEGORIES, CONTRACTOR_TIERS, TECH_SKILLS
from db import upsert_jobs

# Realistic demand weights — higher = appears more often in job postings
_WEIGHTS = {
    "Python": 10, "JavaScript": 10, "React": 9, "TypeScript": 7, "Node.js": 8,
    "PHP": 6, "WordPress": 7, "Django": 5, "FastAPI": 4, "Vue.js": 5,
    "Angular": 4, "AWS": 6, "Docker": 5, "Machine Learning": 6,
    "OpenAI": 7, "LLM": 6, "LangChain": 5, "RAG": 4,
    "PostgreSQL": 5, "MySQL": 5, "MongoDB": 4, "SQL": 6,
    "React Native": 5, "Flutter": 4,
    "Java": 5, "Spring Boot": 3, "Go": 3, "Rust": 2, "C#": 3, "C++": 2,
    "Data Analysis": 5, "Pandas": 4, "Power BI": 2, "Tableau": 2,
    "REST API": 6, "GraphQL": 3, "Microservices": 4,
    "DevOps": 4, "CI/CD": 3, "Kubernetes": 3, "Terraform": 2, "Linux": 4,
    "Web Scraping": 4, "Selenium": 3, "Playwright": 3,
    "iOS": 3, "Android": 3, "Swift": 2, "Kotlin": 2,
    "Shopify": 4, "Webflow": 3, "WooCommerce": 3,
    "Solidity": 2, "Web3": 2, "Blockchain": 2,
    "Computer Vision": 3, "NLP": 3, "Deep Learning": 4,
    "TensorFlow": 3, "PyTorch": 3,
    "Azure": 3, "GCP": 3,
    "Elasticsearch": 2, "Redis": 2, "Supabase": 3,
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
    3, 2, 2, 2, 3, 4,   # 00-05 UTC
    6, 9, 12, 16, 20,   # 06-10 UTC
    24, 28, 30, 28, 25, # 11-15 UTC  ← peak (9am–noon US East)
    22, 18, 15, 13, 11, # 16-20 UTC
    9,  7,  5,          # 21-23 UTC
]

# Day-of-week weights (Mon=0 ... Sun=6)
_DAY_WEIGHTS = [22, 22, 20, 18, 12, 4, 2]

_JOBS_PER_DAY_BASE = 280


def generate_demo_data(days: int = 14) -> int:
    now = datetime.now(timezone.utc)
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
            skills = list({
                s for s in random.choices(_SKILL_LIST, weights=_SKILL_PROBS, k=skill_count * 2)
            })[:skill_count]

            budget_type, budget_amount = _random_budget()

            jobs.append({
                "id": str(uuid.uuid4()),
                "title": f"Job {uuid.uuid4().hex[:8]}",
                "published_at": pub_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "category": category,
                "contractor_tier": tier,
                "budget_type": budget_type,
                "budget_amount": round(budget_amount, 2),
                "skills": json.dumps(skills),
            })

    upsert_jobs(jobs)
    return len(jobs)


def _random_budget() -> tuple:
    if random.random() < 0.60:
        roll = random.random()
        if roll < 0.40:   amount = random.uniform(100, 500)
        elif roll < 0.75: amount = random.uniform(500, 1_500)
        elif roll < 0.93: amount = random.uniform(1_500, 5_000)
        else:             amount = random.uniform(5_000, 15_000)
        return "FIXED", amount
    else:
        roll = random.random()
        if roll < 0.30:   amount = random.uniform(10, 25)
        elif roll < 0.65: amount = random.uniform(25, 50)
        elif roll < 0.85: amount = random.uniform(50, 100)
        else:             amount = random.uniform(100, 150)
        return "HOURLY", amount
