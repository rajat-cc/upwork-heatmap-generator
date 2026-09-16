from __future__ import annotations

import pytest

from taxonomies.countries import iso2_to_name, to_iso2


@pytest.mark.parametrize(
    "raw, code",
    [
        ("United States", "US"),
        ("USA", "US"),
        ("us", "US"),
        ("United States of America", "US"),
        ("United Kingdom", "GB"),
        ("GBR", "GB"),
        ("UK", "GB"),
        ("United Arab Emirates", "AE"),
        ("UAE", "AE"),
        ("India", "IN"),
        ("Türkiye", "TR"),
        ("Turkey", "TR"),
        ("  Canada ", "CA"),
        ("", ""),
        (None, ""),
    ],
)
def test_to_iso2_known_spellings(raw, code):
    assert to_iso2(raw) == code


def test_unknown_spelling_passes_through_unchanged():
    assert to_iso2("Atlantis") == "Atlantis"


def test_iso2_to_name_round_trip():
    assert iso2_to_name("US") == "United States"
    assert iso2_to_name("gb") == "United Kingdom"
    assert iso2_to_name("Atlantis") == "Atlantis"
    assert iso2_to_name("") == ""


def test_upsert_normalises_country_on_every_write_path(isolated_db):
    from db import get_conn, init_db, upsert_jobs

    init_db()
    upsert_jobs([{"id": "j1", "client_country": "USA"}, {"id": "j2", "client_country": "Atlantis"}])
    with get_conn() as conn:
        rows = {
            r["id"]: r["client_country"]
            for r in conn.execute("SELECT id, client_country FROM jobs")
        }
    assert rows == {"j1": "US", "j2": "Atlantis"}
