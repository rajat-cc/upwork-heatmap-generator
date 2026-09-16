"""Aggregations powering the general dashboard.

Four independent analyses, each filtered by a `days`-window cutoff:
  - skills_stats:        top skills with trend, opp score, competition
  - client_stats:        country breakdown, quality buckets, hire rates
  - hourly_matrix:       7×24 jobs/hour grid in the requested timezone
  - shift_recommendation: best 8h BD window from the matrix

Money figures are medians with p25–p75 bands (budgets are heavy-tailed).
Trend % is guarded: it only appears when the window holds at least three
completed fetch runs and both halves have enough promptly-discovered jobs;
otherwise it would measure when sweeps ran, not what the market did.

Returns dict-shaped results to preserve the existing renderer/exporter
contracts. A future refactor can dataclass these too.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import pytz

import config
from core.scoring import get_scoring
from core.stats import band, median
from db import count_fetch_runs, get_conn, hours_between
from taxonomies.countries import iso2_to_name
from taxonomies.skills import normalize_skill

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Minimum jobs in each half before we trust the trend number.
_TREND_MIN_SAMPLE = 5
# Minimum completed fetch runs inside the window before trend is shown at all.
_TREND_MIN_RUNS = 3
# A job counts toward the trend only if it was discovered within this many
# hours of being published; late discoveries reflect sweep timing.
_TREND_PROMPT_HOURS = 48


def skills_stats(days: int = 14, categories: list | None = None) -> list[dict]:
    now = datetime.now(UTC)
    full_cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    half_cutoff = (now - timedelta(days=days // 2)).strftime("%Y-%m-%dT%H:%M:%S")
    runs = count_fetch_runs(since_iso=full_cutoff)

    query = """
        SELECT skills, contractor_tier, budget_type, budget_amount,
               budget_min, budget_max, total_applicants, published_at, first_seen_at
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
        lag = hours_between(row["published_at"], row["first_seen_at"])
        prompt = lag is not None and 0 <= lag <= _TREND_PROMPT_HOURS

        for skill in raw_skills:
            sk = normalize_skill(skill)
            if not sk:
                continue
            if sk not in aggregated:
                aggregated[sk] = {
                    "count": 0,
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
            d["count"] += 1
            if prompt:
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
        total = d["count"]

        if runs < _TREND_MIN_RUNS:
            trend_pct, trend_reason = None, "runs"
        elif old >= _TREND_MIN_SAMPLE and new >= _TREND_MIN_SAMPLE:
            trend_pct, trend_reason = round(((new - old) / old) * 100), "ok"
        else:
            trend_pct, trend_reason = None, "sample"

        tier_total = sum(d["tiers"].values()) or 1
        h25, h50, h75 = band(d["hourly_budgets"])
        f25, f50, f75 = band(d["fixed_budgets"])
        med_proposals = median(d["applicants"])

        contract_total = d["hourly_count"] + d["fixed_count"]
        hourly_pct = round(d["hourly_count"] / contract_total * 100) if contract_total > 0 else 0

        results.append(
            {
                "skill": sk,
                "count": total,
                "trend_pct": trend_pct,
                "trend_reason": trend_reason,
                "med_hourly": h50,
                "hourly_p25": h25,
                "hourly_p75": h75,
                "med_fixed": f50,
                "fixed_p25": f25,
                "fixed_p75": f75,
                "hourly_pct": hourly_pct,
                "med_proposals": med_proposals,
                "entry_pct": round(d["tiers"].get("ENTRY_LEVEL", 0) / tier_total * 100),
                "mid_pct": round(d["tiers"].get("INTERMEDIATE", 0) / tier_total * 100),
                "exp_pct": round(d["tiers"].get("EXPERT", 0) / tier_total * 100),
            }
        )

    if results:
        sk = get_scoring().skills
        w_demand = float(sk.get("demand_weight", 0.45))
        w_budget = float(sk.get("budget_weight", 0.30))
        w_comp = float(sk.get("competition_weight", 0.25))
        comp_scale = float(sk.get("competition_scale", 10.0))
        max_count = max(r["count"] for r in results) or 1
        max_budget = max(max(r["med_hourly"], r["med_fixed"]) for r in results) or 1

        for r in results:
            demand_norm = r["count"] / max_count
            best_budget = r["med_hourly"] if r["med_hourly"] > 0 else r["med_fixed"]
            budget_norm = best_budget / max_budget
            competition_penalty = 1 / (1 + r["med_proposals"] / comp_scale)
            r["opportunity_score"] = round(
                (demand_norm * w_demand + budget_norm * w_budget + competition_penalty * w_comp)
                * 100
            )

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:50]


def client_stats(days: int = 14) -> dict:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT client_country, client_verified, client_total_spent,
                   client_total_hires, client_feedback, budget_amount, budget_type, hire_rate
            FROM jobs WHERE published_at >= ?
            """,
            [cutoff],
        ).fetchall()

    country_data: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "verified": 0,
            "total_spent": 0.0,
            "budgets": [],
            "hires": [],
            "hire_rates": [],
        }
    )

    quality_buckets = {"champion": 0, "active": 0, "new": 0, "risky": 0}
    verified_total = 0
    hire_rates: list[float] = []
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
        if row["hire_rate"] is not None:
            country_data[country]["hire_rates"].append(float(row["hire_rate"]))
            hire_rates.append(float(row["hire_rate"]))

        hires = int(row["client_total_hires"] or 0)
        verified = bool(row["client_verified"])
        # Order matters: "new" (verified, never hired) must be tested before
        # the catch-all, or verified first-time clients are mislabelled risky.
        if verified and spent >= 10000 and hires >= 5:
            quality_buckets["champion"] += 1
        elif verified and hires >= 1:
            quality_buckets["active"] += 1
        elif verified and hires == 0:
            quality_buckets["new"] += 1
        else:
            quality_buckets["risky"] += 1

    countries: list[dict] = []
    for country, d in country_data.items():
        avg_budget = sum(d["budgets"]) / len(d["budgets"]) if d["budgets"] else 0
        avg_hires = sum(d["hires"]) / len(d["hires"]) if d["hires"] else 0
        verified_pct = round(d["verified"] / d["count"] * 100) if d["count"] else 0
        countries.append(
            {
                "country": country,
                "country_name": iso2_to_name(country) if country != "Unknown" else "Unknown",
                "count": d["count"],
                "verified_pct": verified_pct,
                "avg_budget": avg_budget,
                "avg_hires": avg_hires,
                "med_hire_rate": median(d["hire_rates"]) if d["hire_rates"] else None,
                "hire_rate_n": len(d["hire_rates"]),
            }
        )

    countries.sort(key=lambda x: x["count"], reverse=True)

    return {
        "countries": countries[:15],
        "quality": quality_buckets,
        "verified_pct": round(verified_total / total * 100) if total else 0,
        "med_hire_rate": median(hire_rates) if hire_rates else None,
        "hire_rate_n": len(hire_rates),
        "total_jobs": total,
    }


def hourly_matrix(tz_name: str = "UTC", days: int = 14) -> list[list[float]]:
    tz = pytz.timezone(tz_name)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

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


def good_segments(days: int = 90) -> set[str] | None:
    """Client segments where your shrunk win rate is at least the overall one; None when no data."""
    from features.funnel.analyzer import analyze  # lazy: features import each other only here

    report = analyze(days)
    rows = [r for r in report.segments.get("client_segment", []) if r.submitted]
    if not rows:
        return None
    return {r.value for r in rows if r.win.shrunk >= report.win.shrunk} or None


def winnable_matrix(
    tz_name: str = "UTC",
    days: int = 14,
    *,
    max_applicants: int | None = None,
    segments: set[str] | None = None,
) -> list[list[float]]:
    """7×24 grid of postings that were still winnable when first seen.

    A posting counts when its first snapshot showed at most `max_applicants`
    applicants and, when your funnel has data, its client segment is one you
    convert in at least as well as average.
    """
    from core.scoring import client_segment_of

    limit = config.WINNABLE_MAX_APPLICANTS if max_applicants is None else max_applicants
    tz = pytz.timezone(tz_name)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT j.*, (SELECT s.total_applicants FROM job_snapshots s
                          WHERE s.job_id = j.id ORDER BY s.observed_at LIMIT 1) AS first_applicants
              FROM jobs j WHERE j.published_at >= ?
            """,
            (cutoff,),
        ).fetchall()

    from core.models import Job

    matrix = [[0] * 24 for _ in range(7)]
    for row in rows:
        first = row["first_applicants"]
        if first is None or int(first) > limit:
            continue
        job = Job.from_row(row)
        if segments is not None and client_segment_of(job) not in segments:
            continue
        try:
            dt_utc = datetime.fromisoformat(
                job.published_at + "+00:00" if "+" not in job.published_at else job.published_at
            )
        except ValueError:
            continue
        local = dt_utc.astimezone(tz)
        matrix[local.weekday()][local.hour] += 1
    weeks = max(1, days / 7)
    return [[round(matrix[wd][hr] / weeks, 1) for hr in range(24)] for wd in range(7)]


def shift_recommendation(matrix: list[list[float]]) -> dict:
    weekday_matrix = matrix[:5]
    hourly_avg = [sum(weekday_matrix[wd][hr] for wd in range(5)) / 5 for hr in range(24)]

    window = 8
    doubled = hourly_avg + hourly_avg
    best_sum, shift_start = -1, 8
    for start in range(24):
        window_sum = sum(doubled[start : start + window])
        if window_sum > best_sum:
            best_sum = window_sum
            shift_start = start
    shift_end = (shift_start + window - 1) % 24

    peak_hour = hourly_avg.index(max(hourly_avg)) if hourly_avg else 0

    day_totals = [sum(matrix[wd]) for wd in range(7)]
    best_day = DAYS_OF_WEEK[day_totals.index(max(day_totals))]
    worst_day = DAYS_OF_WEEK[day_totals.index(min(day_totals))]

    return {
        "shift_start": shift_start,
        "shift_end": shift_end,
        "peak_hour": peak_hour,
        "peak_volume": round(max(hourly_avg)) if hourly_avg else 0,
        "best_day": best_day,
        "worst_day": worst_day,
        "hourly_avg": hourly_avg,
    }
