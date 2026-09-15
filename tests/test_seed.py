"""The demo seeder must produce rows every downstream lens can use."""

from __future__ import annotations

import importlib
import json


def test_seed_inserts_complete_rows(isolated_db):
    import seed

    importlib.reload(seed)
    from db import get_conn, init_db

    init_db()
    n = seed.generate_demo_data(days=1)
    assert n > 0

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        fts = conn.execute(
            "SELECT count(*) FROM jobs_fts WHERE jobs_fts MATCH 'developer OR workflow OR pipeline'"
        ).fetchone()[0]
    assert len(rows) == n
    for row in rows:
        assert row["description"]
        assert row["client_country"]
        assert row["duration_label"]
        assert row["budget_type"] in {"HOURLY", "FIXED"}
        assert row["discovered_via_search"] == "seed"
        assert isinstance(json.loads(row["skills"]), list)
    # Hourly rows carry a min/max band, fixed rows collapse to the amount.
    hourly = [r for r in rows if r["budget_type"] == "HOURLY"]
    assert hourly and all(0 < r["budget_min"] < r["budget_max"] for r in hourly)
    assert fts > 0
