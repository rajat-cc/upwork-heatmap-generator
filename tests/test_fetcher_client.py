"""GraphQL client behaviour: partial data, auth refresh, backoff, filters, paging.

Every test replays canned responses through an injected transport, so nothing
here touches the network or the real token cache.
"""

from __future__ import annotations

import pytest
import requests

from core.ratelimit import TokenBucket


class Seq:
    """Transport that returns queued responses in order (or raises queued exceptions)."""

    def __init__(self, items):
        self.items = list(items)
        self.calls: list[tuple[dict, dict]] = []

    def __call__(self, body, headers):
        self.calls.append((body, headers))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def f(monkeypatch):
    """A freshly loaded `fetcher` with auth and rate limiting stubbed out."""
    import fetcher

    monkeypatch.setattr(fetcher.auth, "get_access_token", lambda: "tok1")
    refreshes: list[int] = []

    def _force_refresh():
        refreshes.append(1)
        return "tok2"

    monkeypatch.setattr(fetcher.auth, "force_refresh", _force_refresh)
    monkeypatch.setattr(fetcher, "_bucket", TokenBucket(rate_per_sec=1e9, burst=10**6))
    fetcher._test_refreshes = refreshes  # type: ignore[attr-defined]
    return fetcher


def _no_sleep(_s: float) -> None:
    return None


# ─── gql() ──────────────────────────────────────────────────────────────────


def test_partial_data_is_used_when_error_is_not_permission_or_validation(f):
    t = Seq(
        [
            f.Response(
                200,
                {
                    "data": {"x": 1},
                    "errors": [
                        {"message": "Cannot return null for non-nullable field experienceLevel"}
                    ],
                },
            )
        ]
    )
    assert f.gql("query Q { x }", transport=t, sleep=_no_sleep) == {"x": 1}


def test_permission_error_raises_even_with_data(f):
    t = Seq(
        [
            f.Response(
                200,
                {
                    "data": {"x": None},
                    "errors": [
                        {"message": "Your OAuth2 permissions do not allow access to this field"}
                    ],
                },
            )
        ]
    )
    with pytest.raises(f.GraphQLError) as exc:
        f.gql("query Q { x }", transport=t, sleep=_no_sleep)
    assert exc.value.is_permission_error


def test_validation_error_raises(f):
    t = Seq(
        [
            f.Response(
                200,
                {
                    "data": {"x": 1},
                    "errors": [
                        {
                            "message": "Validation error",
                            "extensions": {"classification": "ValidationError"},
                        }
                    ],
                },
            )
        ]
    )
    with pytest.raises(f.GraphQLError) as exc:
        f.gql("query Q { x }", transport=t, sleep=_no_sleep)
    assert exc.value.is_validation_error


def test_http_401_refreshes_token_once_and_retries(f):
    t = Seq([f.Response(401, None, "unauthorized"), f.Response(200, {"data": {"ok": True}})])
    assert f.gql("query Q { ok }", transport=t, sleep=_no_sleep) == {"ok": True}
    assert len(f._test_refreshes) == 1
    assert t.calls[0][1]["Authorization"] == "Bearer tok1"
    assert t.calls[1][1]["Authorization"] == "Bearer tok2"


def test_http_401_twice_raises_after_single_refresh(f):
    t = Seq([f.Response(401), f.Response(403)])
    with pytest.raises(f.GraphQLError, match="make auth"):
        f.gql("query Q { ok }", transport=t, sleep=_no_sleep)
    assert len(f._test_refreshes) == 1


def test_token_rejected_message_triggers_refresh(f):
    t = Seq(
        [
            f.Response(200, {"data": None, "errors": [{"message": "Invalid token provided"}]}),
            f.Response(200, {"data": {"ok": True}}),
        ]
    )
    assert f.gql("query Q { ok }", transport=t, sleep=_no_sleep) == {"ok": True}
    assert len(f._test_refreshes) == 1


def test_transient_statuses_back_off_then_succeed(f):
    slept: list[float] = []
    t = Seq([f.Response(503), f.Response(429), f.Response(200, {"data": {"ok": True}})])
    out = f.gql("query Q { ok }", transport=t, sleep=slept.append)
    assert out == {"ok": True}
    assert len(slept) == 2
    assert all(0 <= s <= f.FETCH_BACKOFF_MAX for s in slept)


def test_transient_retries_exhausted(f):
    t = Seq([f.Response(502), f.Response(502)])
    with pytest.raises(f.TransientError):
        f.gql("query Q { ok }", transport=t, max_retries=1, sleep=_no_sleep)


def test_connection_error_is_retried(f):
    t = Seq([requests.ConnectionError("boom"), f.Response(200, {"data": {"ok": True}})])
    assert f.gql("query Q { ok }", transport=t, sleep=_no_sleep) == {"ok": True}


def test_tenant_header_only_when_org_id_known(f):
    t = Seq([f.Response(200, {"data": {}}), f.Response(200, {"data": {}})])
    f.gql("query Q { ok }", transport=t, sleep=_no_sleep)
    f.gql("query Q { ok }", transport=t, org_id="org-1", sleep=_no_sleep)
    assert "X-Upwork-API-TenantId" not in t.calls[0][1]
    assert t.calls[1][1]["X-Upwork-API-TenantId"] == "org-1"


# ─── build_filter() ─────────────────────────────────────────────────────────


def test_build_filter_uses_confirmed_field_names(f):
    flt = f.build_filter("n8n", ["c1"], ["s1"], ["k1"], first=25, after="50")
    assert flt == {
        "pagination_eq": {"first": 25, "after": "50"},
        "searchExpression_eq": "n8n",
        "categoryIds_any": ["c1"],
        "subcategoryIds_any": ["s1"],
        "ontologySkillIds_all": ["k1"],
    }


def test_build_filter_omits_empty_parts(f):
    assert f.build_filter() == {"pagination_eq": {"first": f.PAGE_SIZE, "after": "0"}}


# ─── FixtureTransport ───────────────────────────────────────────────────────


def test_fixture_transport_keys_by_operation_and_name_variable(f):
    key = f.FixtureTransport.key_for(
        {"query": "query IntrospectType($name: String!) { __type(name: $name) { name } }",
         "variables": {"name": "Foo"}}
    )  # fmt: skip
    assert key == "IntrospectType:Foo"
    assert f.FixtureTransport.key_for({"query": "query OrgId { organization { id } }"}) == "OrgId"
    assert f.FixtureTransport.key_for({"query": "{ organization { id } }"}) == "anonymous"


def test_fixture_transport_status_entries(f):
    t = f.FixtureTransport({"Q": {"status": 503, "payload": None}})
    resp = t({"query": "query Q { x }"}, {})
    assert resp.status_code == 503


# ─── fetch_jobs() ───────────────────────────────────────────────────────────


def _node(i: int, **over):
    base = {
        "id": f"job-{i}",
        "title": f"Job {i}",
        "description": "desc",
        "publishedDateTime": f"2026-09-1{i}T10:00:00+0000",
        "experienceLevel": None,
        "category": "Web, Mobile & Software Dev",
        "durationLabel": "1 to 3 months",
        "premium": False,
        "enterprise": False,
        "totalApplicants": 3,
        "skills": [{"name": "python"}],
        "amount": {"rawValue": "500"},
        "hourlyBudgetMin": {"rawValue": None},
        "hourlyBudgetMax": {"rawValue": None},
        "client": {
            "totalHires": 2,
            "totalPostedJobs": 4,
            "totalSpent": {"rawValue": "1000"},
            "verificationStatus": "VERIFIED",
            "totalFeedback": 4.5,
            "location": {"country": "United States"},
        },
    }
    base.update(over)
    return base


def test_fetch_jobs_skips_null_nodes_and_records_the_run(isolated_db, f):
    from db import get_conn, init_db

    init_db()
    t = f.FixtureTransport(
        {
            "OrgId": {"data": {"organization": {"id": "org-9"}}},
            "SearchJobs": {
                "data": {
                    "marketplaceJobPostingsSearch": {
                        "totalCount": 3,
                        "pageInfo": {"hasNextPage": False, "endCursor": "3"},
                        "edges": [{"node": _node(1)}, {"node": None}, {"node": _node(2)}],
                    }
                },
                "errors": [
                    {"message": "Cannot return null for non-nullable field experienceLevel"}
                ],
            },
        }
    )

    n = f.fetch_jobs("python", label="watch:python", transport=t, sleep=_no_sleep)
    assert n == 2

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, contractor_tier, discovered_via_search FROM jobs"
        ).fetchall()
        runs = conn.execute(
            "SELECT search_term, status, jobs_seen, jobs_new FROM fetch_runs"
        ).fetchall()
    assert sorted(r["id"] for r in rows) == ["job-1", "job-2"]
    assert {r["contractor_tier"] for r in rows} == {"UNKNOWN"}  # nulled tier stored honestly
    assert {r["discovered_via_search"] for r in rows} == {"watch:python"}
    assert [tuple(r) for r in runs] == [("watch:python", "done", 2, 2)]
    # The search page carried the tenant header resolved from OrgId.
    search_calls = [b for b in t.calls if f.FixtureTransport.key_for(b) == "SearchJobs"]
    assert search_calls[0]["variables"]["filter"]["searchExpression_eq"] == "python"


def test_fetch_jobs_marks_run_error_and_reraises(isolated_db, f):
    from db import get_conn, init_db

    init_db()
    t = f.FixtureTransport(
        {
            "OrgId": {"data": {"organization": {"id": "org-9"}}},
            "SearchJobs": {
                "data": None,
                "errors": [{"message": "Your OAuth2 permissions do not allow access"}],
            },
        }
    )
    with pytest.raises(f.GraphQLError):
        f.fetch_jobs("python", transport=t, sleep=_no_sleep)
    with get_conn() as conn:
        runs = conn.execute("SELECT status FROM fetch_runs").fetchall()
    assert [r["status"] for r in runs] == ["error"]
