import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytz

from config import TECH_SKILLS
from db import get_conn

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Minimum jobs in the "old" half before we trust the trend number
_TREND_MIN_SAMPLE = 5


def skills_stats(days: int = 14, categories: list = None) -> list:
    now = datetime.now(timezone.utc)
    full_cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    half_cutoff = (now - timedelta(days=days // 2)).strftime("%Y-%m-%dT%H:%M:%S")

    query = """
        SELECT skills, contractor_tier, budget_type, budget_amount,
               budget_min, budget_max, total_applicants, published_at
        FROM jobs WHERE published_at >= ?
    """
    params = [full_cutoff]
    if categories:
        query += f" AND category IN ({','.join('?' * len(categories))})"
        params += list(categories)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    aggregated = {}

    for row in rows:
        raw_skills = json.loads(row["skills"] or "[]")
        tier = row["contractor_tier"] or "UNKNOWN"
        btype = row["budget_type"] or "UNKNOWN"
        budget = float(row["budget_amount"] or 0)
        applicants = int(row["total_applicants"] or 0)
        is_recent = (row["published_at"] or "") >= half_cutoff

        for skill in raw_skills:
            sk = _normalize_skill(skill)
            if not sk:
                continue
            if sk not in aggregated:
                aggregated[sk] = {
                    "count_old": 0,
                    "count_new": 0,
                    "hourly_budgets": [],
                    "fixed_budgets": [],
                    "applicants": [],
                    "tiers": defaultdict(int),
                    "hourly_count": 0,
                    "fixed_count": 0,
                }
            d = aggregated[sk]
            if is_recent:
                d["count_new"] += 1
            else:
                d["count_old"] += 1

            if btype == "HOURLY" and budget > 0:
                d["hourly_budgets"].append(budget)
                d["hourly_count"] += 1
            elif btype == "FIXED" and budget > 0:
                d["fixed_budgets"].append(budget)
                d["fixed_count"] += 1

            if applicants > 0:
                d["applicants"].append(applicants)
            d["tiers"][tier] += 1

    results = []
    for sk, d in aggregated.items():
        old, new = d["count_old"], d["count_new"]
        total = old + new

        # Trend: suppress if old-half sample is too small to be meaningful
        if old >= _TREND_MIN_SAMPLE:
            trend_pct = round(((new - old) / old) * 100)
        elif new > 0 and old == 0:
            trend_pct = None  # new skill, no baseline
        else:
            trend_pct = None

        tier_total = sum(d["tiers"].values()) or 1
        avg_hourly = sum(d["hourly_budgets"]) / len(d["hourly_budgets"]) if d["hourly_budgets"] else 0
        avg_fixed = sum(d["fixed_budgets"]) / len(d["fixed_budgets"]) if d["fixed_budgets"] else 0
        avg_proposals = sum(d["applicants"]) / len(d["applicants"]) if d["applicants"] else 0

        contract_total = d["hourly_count"] + d["fixed_count"]
        hourly_pct = round(d["hourly_count"] / contract_total * 100) if contract_total > 0 else 0

        results.append({
            "skill":        sk,
            "count":        total,
            "trend_pct":    trend_pct,
            "avg_hourly":   avg_hourly,
            "avg_fixed":    avg_fixed,
            "hourly_pct":   hourly_pct,
            "avg_proposals": avg_proposals,
            "entry_pct":    round(d["tiers"].get("ENTRY_LEVEL", 0) / tier_total * 100),
            "mid_pct":      round(d["tiers"].get("INTERMEDIATE", 0) / tier_total * 100),
            "exp_pct":      round(d["tiers"].get("EXPERT", 0) / tier_total * 100),
        })

    # Compute opportunity score across all skills (normalised 0–100)
    if results:
        max_count = max(r["count"] for r in results) or 1
        max_budget = max(
            max(r["avg_hourly"], r["avg_fixed"]) for r in results
        ) or 1

        for r in results:
            demand_norm = r["count"] / max_count
            # Use whichever budget is non-zero; if both, prefer hourly (ongoing work)
            best_budget = r["avg_hourly"] if r["avg_hourly"] > 0 else r["avg_fixed"]
            budget_norm = best_budget / max_budget
            # Low competition = high score; guard against zero proposals
            competition_penalty = 1 / (1 + r["avg_proposals"] / 10)
            r["opportunity_score"] = round(
                (demand_norm * 0.45 + budget_norm * 0.30 + competition_penalty * 0.25) * 100
            )

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:50]


def client_stats(days: int = 14) -> dict:
    """Returns country breakdown and client quality summary."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT client_country, client_verified, client_total_spent,
                   client_total_hires, client_feedback, budget_amount, budget_type
            FROM jobs WHERE published_at >= ?
            """,
            [cutoff],
        ).fetchall()

    country_data = defaultdict(lambda: {
        "count": 0, "verified": 0, "total_spent": 0.0,
        "budgets": [], "hires": [],
    })

    quality_buckets = {"champion": 0, "active": 0, "new": 0, "risky": 0}
    verified_total = 0
    total = len(rows)

    for row in rows:
        country = row["client_country"] or "Unknown"
        country_data[country]["count"] += 1
        if row["client_verified"]:
            country_data[country]["verified"] += 1
            verified_total += 1
        spent = float(row["client_total_spent"] or 0)
        country_data[country]["total_spent"] += spent
        if row["budget_amount"] and row["budget_amount"] > 0:
            country_data[country]["budgets"].append(float(row["budget_amount"]))
        if row["client_total_hires"] is not None:
            country_data[country]["hires"].append(int(row["client_total_hires"]))

        # Client quality classification
        hires = int(row["client_total_hires"] or 0)
        verified = bool(row["client_verified"])
        if spent >= 10000 and hires >= 5 and verified:
            quality_buckets["champion"] += 1
        elif hires >= 1 and verified:
            quality_buckets["active"] += 1
        elif not verified or hires == 0:
            quality_buckets["risky"] += 1
        else:
            quality_buckets["new"] += 1

    # Build ranked country list
    countries = []
    for country, d in country_data.items():
        avg_budget = sum(d["budgets"]) / len(d["budgets"]) if d["budgets"] else 0
        avg_hires = sum(d["hires"]) / len(d["hires"]) if d["hires"] else 0
        verified_pct = round(d["verified"] / d["count"] * 100) if d["count"] else 0
        countries.append({
            "country":       country,
            "count":         d["count"],
            "verified_pct":  verified_pct,
            "avg_budget":    avg_budget,
            "avg_hires":     avg_hires,
        })

    countries.sort(key=lambda x: x["count"], reverse=True)

    return {
        "countries":      countries[:15],
        "quality":        quality_buckets,
        "verified_pct":   round(verified_total / total * 100) if total else 0,
        "total_jobs":     total,
    }


def hourly_matrix(tz_name: str = "UTC", days: int = 14) -> list:
    tz = pytz.timezone(tz_name)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT published_at FROM jobs WHERE published_at >= ?", [cutoff]
        ).fetchall()

    matrix = [[0] * 24 for _ in range(7)]

    for row in rows:
        raw = row["published_at"]
        if not raw:
            continue
        try:
            dt_utc = datetime.fromisoformat(raw + "+00:00" if "+" not in raw else raw)
            dt_local = dt_utc.astimezone(tz)
            matrix[dt_local.weekday()][dt_local.hour] += 1
        except (ValueError, AttributeError):
            continue

    weeks = max(1, days / 7)
    return [[round(matrix[wd][hr] / weeks, 1) for hr in range(24)] for wd in range(7)]


def shift_recommendation(matrix: list) -> dict:
    weekday_matrix = matrix[:5]
    hourly_avg = [
        sum(weekday_matrix[wd][hr] for wd in range(5)) / 5
        for hr in range(24)
    ]

    max_vol = max(hourly_avg) if hourly_avg else 1

    # Best contiguous 8-hour BD window (sliding, wraps around midnight)
    window = 8
    doubled = hourly_avg + hourly_avg
    best_sum, shift_start = -1, 8
    for start in range(24):
        window_sum = sum(doubled[start:start + window])
        if window_sum > best_sum:
            best_sum = window_sum
            shift_start = start
    shift_end = (shift_start + window - 1) % 24

    peak_hour = hourly_avg.index(max(hourly_avg))

    day_totals = [sum(matrix[wd]) for wd in range(7)]
    best_day = DAYS_OF_WEEK[day_totals.index(max(day_totals))]
    worst_day = DAYS_OF_WEEK[day_totals.index(min(day_totals))]

    return {
        "shift_start":  shift_start,
        "shift_end":    shift_end,
        "peak_hour":    peak_hour,
        "peak_volume":  round(max(hourly_avg)),
        "best_day":     best_day,
        "worst_day":    worst_day,
        "hourly_avg":   hourly_avg,
    }


# ─── Skill normalisation ─────────────────────────────────────────────────────

_SLUG_MAP = {
    "amazon-web-services": "AWS", "amazon-ec2": "AWS",
    "amazon-s3": "AWS", "amazon-lambda": "AWS",
    "google-cloud-platform": "GCP",
    "microsoft-azure": "Azure",
    "node.js": "Node.js", "nodejs": "Node.js",
    "react.js": "React", "react-js": "React",
    "next.js": "Next.js",
    "vue.js": "Vue.js",
    "angular.js": "Angular", "angularjs": "Angular",
    "fastapi": "FastAPI",
    "spring-boot": "Spring Boot",
    "react-native": "React Native",
    "machine-learning": "Machine Learning",
    "deep-learning": "Deep Learning",
    "natural-language-processing": "NLP",
    "computer-vision": "Computer Vision",
    "large-language-model": "LLM",
    "generative-ai": "LLM",
    "artificial-intelligence": "Machine Learning",
    "api-integration": "REST API", "api-development": "REST API",
    "restful-api": "REST API", "rest-api": "REST API",
    "ci-cd": "CI/CD", "cicd": "CI/CD",
    "automated-deployment": "CI/CD", "continuous-integration": "CI/CD",
    "microsoft-power-bi": "Power BI", "power-bi": "Power BI",
    "data-analysis": "Data Analysis", "data-science": "Data Analysis",
    "data-visualization": "Tableau",
    "html5": "HTML", "html": "HTML",
    "css3": "CSS", "css": "CSS",
    "web3-js": "Web3", "solidity": "Solidity", "blockchain": "Blockchain",
    "mobile-app-development": "React Native",
    "ios-development": "iOS", "android-development": "Android",
    "swift-programming-language": "Swift", "kotlin": "Kotlin",
    "flutter": "Flutter", "firebase": "Firebase",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
    "redis": "Redis", "elasticsearch": "Elasticsearch", "supabase": "Supabase",
    "docker": "Docker", "kubernetes": "Kubernetes", "terraform": "Terraform",
    "linux": "Linux", "git": "Git",
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
    "php": "PHP", "laravel": "Laravel", "django": "Django",
    "flask": "Flask", "java": "Java", "go": "Go", "golang": "Go",
    "rust": "Rust", "c#": "C#", "c++": "C++", "swift": "Swift",
    "wordpress": "WordPress", "shopify": "Shopify",
    "woocommerce": "WooCommerce", "webflow": "Webflow",
    "graphql": "GraphQL", "microservices": "Microservices", "devops": "DevOps",
    "pandas": "Pandas", "tableau": "Tableau", "sql": "SQL",
    "web-scraping": "Web Scraping", "selenium": "Selenium",
    "playwright": "Playwright", "langchain": "LangChain",
    "tensorflow": "TensorFlow", "pytorch": "PyTorch",
    "openai": "OpenAI", "openai-api": "OpenAI", "rag": "RAG",
}

_SKIP_SLUGS = {
    "phone", "web-design", "web-programming", "web-application",
    "graphic-design", "microsoft-excel", "project-management",
    "project-management-capability", "strategy", "technology",
    "communication", "customer-service", "leadership", "problem-solving",
    "critical-thinking", "research", "writing", "editing",
    "virtual-assistant", "data-entry", "translation", "accounting",
    "bookkeeping", "logo-design", "ui-design", "ux-design",
    "user-interface-design", "user-experience-design",
    "hybrid", "english", "saas", "cryptocurrency", "startup",
    "agile", "scrum", "software-development", "software-engineering",
    "full-stack-development", "backend-development", "frontend-development",
    "web-development", "app-development", "ecommerce",
}


def _normalize_skill(skill: str) -> str:
    skill = skill.strip()
    if not skill or len(skill) < 2:
        return ""
    slug = skill.lower().replace(" ", "-")
    if slug in _SKIP_SLUGS:
        return ""
    if slug in _SLUG_MAP:
        return _SLUG_MAP[slug]
    skill_lower = skill.lower()
    for known in TECH_SKILLS:
        if skill_lower == known.lower():
            return known
    for known in TECH_SKILLS:
        if known.lower() in skill_lower or skill_lower in known.lower():
            if len(skill) >= 3:
                return known
    if "-" not in skill and len(skill) >= 3 and any(c.isalpha() for c in skill):
        return skill.title()
    return ""
