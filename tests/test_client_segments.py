"""Every client quality segment must be reachable (the old code never produced "new")."""

from __future__ import annotations

from datetime import UTC, datetime


def test_all_four_client_segments_are_reachable(isolated_db):
    from db import init_db, upsert_jobs
    from features.dashboard.analyzer import client_stats

    init_db()
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    upsert_jobs(
        [
            {"id": "champion", "published_at": today, "client_verified": 1,
             "client_total_spent": 20000, "client_total_hires": 9},
            {"id": "active", "published_at": today, "client_verified": 1,
             "client_total_spent": 500, "client_total_hires": 2},
            {"id": "new", "published_at": today, "client_verified": 1,
             "client_total_spent": 0, "client_total_hires": 0},
            {"id": "risky", "published_at": today, "client_verified": 0,
             "client_total_spent": 9000, "client_total_hires": 3},
        ]
    )  # fmt: skip

    cs = client_stats(days=7)
    assert cs["quality"] == {"champion": 1, "active": 1, "new": 1, "risky": 1}
    assert cs["total_jobs"] == 4


def test_hire_rate_is_surfaced_per_country_and_overall(isolated_db):
    from db import init_db, upsert_jobs
    from features.dashboard.analyzer import client_stats

    init_db()
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    upsert_jobs(
        [
            {"id": "a", "published_at": today, "client_country": "USA",
             "client_total_hires": 5, "client_total_posted": 10},
            {"id": "b", "published_at": today, "client_country": "United States",
             "client_total_hires": 1, "client_total_posted": 4},
            {"id": "c", "published_at": today, "client_country": "GBR",
             "client_total_hires": 0, "client_total_posted": 0},  # unknown rate
        ]
    )  # fmt: skip

    cs = client_stats(days=7)
    by_code = {c["country"]: c for c in cs["countries"]}
    assert set(by_code) == {"US", "GB"}  # both US spellings collapsed
    assert by_code["US"]["country_name"] == "United States"
    assert by_code["US"]["count"] == 2
    assert abs(by_code["US"]["med_hire_rate"] - 0.375) < 1e-9  # median of 0.5 and 0.25
    assert by_code["GB"]["med_hire_rate"] is None
    assert cs["hire_rate_n"] == 2
