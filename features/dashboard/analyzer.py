"""Aggregations powering the general dashboard.

Three independent analyses, each filtered by a `days`-window cutoff:
  - skills_stats:        top skills with trend, opp score, competition
  - client_stats:        country breakdown + quality buckets
  - hourly_matrix:       7×24 jobs/hour grid in the requested timezone
  - shift_recommendation: best 8h BD window from the matrix

Returns dict-shaped results to preserve the existing renderer/exporter
contracts. A future refactor can dataclass these too.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytz

from db import get_conn
from taxonomies.skills import normalize_skill

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Minimum jobs in the "old" half before we trust the trend number.
_TREND_MIN_SAMPLE = 5


def skills_stats(days: int = 14, categories: list | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    full_cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    half_cutoff = (now - timedelta(days=days // 2)).strftime("%Y-%m-%dT%H:%M:%S")

    query = """
        SELECT skills, contractor_tier, budget_type, budget_amount,
               budget_min, budget_max, total_applicants, published_at
        FROM jobs WHERE published_at >= ?
    """
    params: list = [full_cutoff]
    if categories:
        query += f" AND category IN ({','.join('?' * len(categories))})"
        params += list(categories)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    aggregated: dict[str, dict] = {}

    for row in rows:
        raw_skills = json.loads(row["skills"] or "[]")
        tier = row["contractor_tier"] or "UNKNOWN"
        btype = row["budget_type"] or "UNKNOWN"
        budget = float(row["budget_amount"] or 0)
        applicants = int(row["total_applicants"] or 0)
        is_recent = (row["published_at"] or "") >= half_cutoff

        for skill in raw_skills:
            sk = normalize_skill(skill)
            if not sk:
                continue
            if sk not in aggregated:
                aggregated[sk] = {
                    "count_old": 0, "count_new": 0,
                    "hourly_budgets": [], "fixed_budgets": [],
                    "applicants": [], "tiers": defaultdict(int),
                    "hourly_count": 0, "fixed_count": 0,
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

    results: list[dict] = []
    for sk, d in aggregated.items():
        old, new = d["count_old"], d["count_new"]
        total = old + new

        if old >= _TREND_MIN_SAMPLE:
            trend_pct = round(((new - old) / old) * 100)
        elif new > 0 and old == 0:
            trend_pct = None
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

    if results:
        max_count = max(r["count"] for r in results) or 1
        max_budget = max(max(r["avg_hourly"], r["avg_fixed"]) for r in results) or 1

        for r in results:
            demand_norm = r["count"] / max_count
            best_budget = r["avg_hourly"] if r["avg_hourly"] > 0 else r["avg_fixed"]
            budget_norm = best_budget / max_budget
            competition_penalty = 1 / (1 + r["avg_proposals"] / 10)
            r["opportunity_score"] = round(
                (demand_norm * 0.45 + budget_norm * 0.30 + competition_penalty * 0.25) * 100
            )

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:50]


def client_stats(days: int = 14) -> dict:
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

    country_data: dict[str, dict] = defaultdict(lambda: {
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

    countries: list[dict] = []
    for country, d in country_data.items():
        avg_budget = sum(d["budgets"]) / len(d["budgets"]) if d["budgets"] else 0
        avg_hires = sum(d["hires"]) / len(d["hires"]) if d["hires"] else 0
        verified_pct = round(d["verified"] / d["count"] * 100) if d["count"] else 0
        countries.append({
            "country":      country,
            "count":        d["count"],
            "verified_pct": verified_pct,
            "avg_budget":   avg_budget,
            "avg_hires":    avg_hires,
        })

    countries.sort(key=lambda x: x["count"], reverse=True)

    return {
        "countries":    countries[:15],
        "quality":      quality_buckets,
        "verified_pct": round(verified_total / total * 100) if total else 0,
        "total_jobs":   total,
    }


def hourly_matrix(tz_name: str = "UTC", days: int = 14) -> list[list[float]]:
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


def shift_recommendation(matrix: list[list[float]]) -> dict:
    weekday_matrix = matrix[:5]
    hourly_avg = [
        sum(weekday_matrix[wd][hr] for wd in range(5)) / 5
        for hr in range(24)
    ]

    window = 8
    doubled = hourly_avg + hourly_avg
    best_sum, shift_start = -1, 8
    for start in range(24):
        window_sum = sum(doubled[start:start + window])
        if window_sum > best_sum:
            best_sum = window_sum
            shift_start = start
    shift_end = (shift_start + window - 1) % 24

    peak_hour = hourly_avg.index(max(hourly_avg)) if hourly_avg else 0

    day_totals = [sum(matrix[wd]) for wd in range(7)]
    best_day = DAYS_OF_WEEK[day_totals.index(max(day_totals))]
    worst_day = DAYS_OF_WEEK[day_totals.index(min(day_totals))]

    return {
        "shift_start":  shift_start,
        "shift_end":    shift_end,
        "peak_hour":    peak_hour,
        "peak_volume":  round(max(hourly_avg)) if hourly_avg else 0,
        "best_day":     best_day,
        "worst_day":    worst_day,
        "hourly_avg":   hourly_avg,
    }
