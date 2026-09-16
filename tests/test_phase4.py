"""Phase 4: capabilities gating, automation lens, LLM tagger, winnable grid, clients, digest, service kinds."""

from __future__ import annotations

import json
import pathlib
import plistlib
from datetime import UTC, datetime, timedelta

from core.models import Job


def _today(hours_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S")


# ─── capabilities → optional search fields ──────────────────────────────────


def test_optional_fields_requested_only_when_live_probe_lists_them(tmp_path, monkeypatch):
    import fetcher
    from core import capabilities

    assert "subcategory" not in fetcher.search_query()  # no probe file → not requested
    probe = tmp_path / "probe.json"
    probe.write_text(
        json.dumps(
            {"offline": False, "capabilities": {"search_node_fields": ["id", "subcategory"]}}
        )
    )
    monkeypatch.setattr(capabilities, "PROBE_PATH", probe)
    assert "subcategory" in fetcher.search_query()
    probe.write_text(
        json.dumps({"offline": True, "capabilities": {"search_node_fields": ["subcategory"]}})
    )
    assert "subcategory" not in fetcher.search_query()  # the offline fixture never counts
    assert (
        fetcher._parse_job({"id": "x", "subcategory": "Scripting & Automation"})["subcategory"]
        == "Scripting & Automation"
    )


# ─── automation lens ────────────────────────────────────────────────────────


def _seed_automation(db):
    from features.n8n import classifier

    rows = [
        {"id": "a1", "title": "n8n voice agent for a dental clinic", "description": "vapi calls", "published_at": _today(1),
         "budget_type": "HOURLY", "budget_min": 40, "budget_max": 60, "budget_amount": 50, "client_verified": 1},
        {"id": "a2", "title": "Make.com scenario HubSpot to Slack", "description": "crm alerts", "published_at": _today(1),
         "budget_type": "FIXED", "budget_amount": 600, "client_verified": 1},
        {"id": "a3", "title": "Zapier automation for a law firm intake", "description": "webhook to airtable",
         "published_at": _today(1), "budget_type": "HOURLY", "budget_min": 30, "budget_max": 45, "budget_amount": 37},
        {"id": "a4", "title": "React dashboard", "description": "frontend only", "published_at": _today(1)},
    ]  # fmt: skip
    db.upsert_jobs(rows)
    classifier.persist(classifier.classify_rows(db.get_jobs_by_ids([r["id"] for r in rows])))


def test_automation_lens_counts_every_platform_and_filters(isolated_db, monkeypatch):
    import config
    import db
    from features.automation import AutomationLens, analyzer

    db.init_db()
    _seed_automation(db)
    monkeypatch.setattr(config, "EXPORTS_DIR", str(isolated_db.parent / "exports"))

    jobs = analyzer.load_jobs(days=7)
    assert sorted(j.id for j in jobs) == ["a1", "a2", "a3"]
    report = analyzer.analyze(jobs)
    counts = dict(report.platform_count)
    assert counts == {"n8n": 1, "Make": 1, "Zapier": 1}
    assert report.platform_matrix["Make"]["CRM Sync"] == 1
    assert report.platform_matrix["n8n"]["Voice / Telephony"] == 1

    only = analyzer.load_jobs(days=7, platforms=["Zapier"])
    assert [j.id for j in only] == ["a3"]

    lens = AutomationLens(["n8n", "Make"])
    rep = lens.analyze(7)
    assert rep.total_jobs == 2 and rep.platforms == ["n8n", "Make"]
    assert lens.export(rep, 7).endswith(".xlsx")


# ─── LLM tagger ─────────────────────────────────────────────────────────────


def test_llm_tagger_labels_untagged_jobs_with_a_fake_runner(isolated_db, monkeypatch):
    import config
    import db
    from features.automation import llm_tagger

    db.init_db()
    monkeypatch.setattr(config, "LLM_TAGGING", True)
    db.upsert_jobs(
        [
            {"id": "u1", "title": "Automate onboarding emails", "description": "for our recruiting team",
             "published_at": _today()},
            {"id": "u2", "title": "Sync orders to sheets", "description": "we sell candles online",
             "published_at": _today(1)},
            {"id": "u3", "title": "n8n for a dental clinic", "description": "patients", "published_at": _today(2)},
        ]
    )  # fmt: skip
    from features.n8n import classifier

    classifier.persist(
        classifier.classify_rows(db.get_jobs_by_ids(["u3"]))
    )  # u3 gets Healthcare by regex

    prompts: list[str] = []

    def fake_runner(prompt: str) -> str:
        prompts.append(prompt)
        return 'Sure: {"u1": ["HR / Recruiting"], "u2": ["E-commerce", "Not A Label"], "u9": ["Legal"]}'

    result = llm_tagger.tag(runner=fake_runner)
    assert result.considered == 2 and result.tagged_jobs == 2 and result.labels == 2
    assert "[u1]" in prompts[0] and "[u3]" not in prompts[0]  # already tagged → not sent
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, label, source, confidence FROM job_classifications WHERE source = 'llm' ORDER BY job_id"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("u1", "HR / Recruiting", "llm", 0.7),
        ("u2", "E-commerce", "llm", 0.7),
    ]

    again = llm_tagger.tag(runner=fake_runner)  # nothing left to tag
    assert again.considered == 0 and len(prompts) == 1


def test_llm_tagger_skips_when_disabled_or_binary_missing(isolated_db, monkeypatch):
    import config
    import db
    from features.automation import llm_tagger

    db.init_db()
    monkeypatch.setattr(config, "LLM_TAGGING", False)
    assert "disabled" in llm_tagger.tag().skipped
    monkeypatch.setattr(config, "LLM_TAGGING", True)
    monkeypatch.setattr(llm_tagger.shutil, "which", lambda _name: None)
    assert "not found" in llm_tagger.tag().skipped


def test_parse_labels_tolerates_prose_and_junk():
    from features.automation.llm_tagger import parse_labels

    assert parse_labels("no json here") == {}
    assert parse_labels(
        '{"a": "Legal", "b": 5, "c": ["Healthcare", "Bogus", "Legal", "Finance / Fintech"]}'
    ) == {
        "a": ["Legal"],
        "c": ["Healthcare", "Legal"],
    }


# ─── winnable heatmap ───────────────────────────────────────────────────────


def test_winnable_matrix_counts_only_low_competition_postings(isolated_db):
    import db
    from features.dashboard.analyzer import hourly_matrix, winnable_matrix

    db.init_db()
    at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    stamp = at.strftime("%Y-%m-%dT%H:%M:%S")
    db.upsert_jobs(
        [
            {"id": "w1", "published_at": stamp, "total_applicants": 3, "client_verified": 1, "client_total_hires": 2},
            {"id": "w2", "published_at": stamp, "total_applicants": 40, "client_verified": 1, "client_total_hires": 2},
            {"id": "w3", "published_at": stamp, "total_applicants": 5, "client_verified": 0},
        ]
    )  # fmt: skip
    all_grid = hourly_matrix("UTC", days=7)
    win = winnable_matrix("UTC", days=7, max_applicants=10)
    assert all_grid[at.weekday()][at.hour] == 3.0
    assert win[at.weekday()][at.hour] == 2.0  # w2 had 40 applicants at first sight
    only_good = winnable_matrix("UTC", days=7, max_applicants=10, segments={"active"})
    assert only_good[at.weekday()][at.hour] == 1.0  # w3 is risky


# ─── clients ────────────────────────────────────────────────────────────────


def test_clients_group_by_fingerprint(isolated_db):
    import db
    from features.clients.analyzer import analyze, fingerprint

    db.init_db()
    same = {"client_country": "US", "client_total_spent": 12000, "client_total_hires": 6, "client_total_posted": 9,
            "client_verified": 1}  # fmt: skip
    db.upsert_jobs(
        [
            {"id": "c1", "title": "n8n crm sync", "published_at": _today(5), **same},
            {"id": "c2", "title": "hubspot crm cleanup", "published_at": _today(1), **same},
            {"id": "c3", "title": "other", "published_at": _today(), "client_country": "GB",
             "client_total_spent": 500, "client_total_hires": 1, "client_total_posted": 2},
            {"id": "c4", "title": "nothing known", "published_at": _today()},  # all-zero fingerprint → skipped
        ]
    )  # fmt: skip
    db.insert_events([{"event_id": "e1", "job_id": "c1", "event": "SUBMITTED", "ts": _today(4) + "Z",
                       "source": "manual", "bid_amount": 50, "bid_type": "hourly", "meta": {}},
                      {"event_id": "e2", "job_id": "c1", "event": "HIRED", "ts": _today(2) + "Z",
                       "source": "manual", "meta": {}}])  # fmt: skip
    assert fingerprint(Job.from_row(db.get_jobs_by_ids(["c4"])[0])) is None

    report = analyze(days=30)
    assert len(report.rows) == 2 and len(report.repeat) == 1
    top = report.rows[0]
    assert top.posts == 2 and top.segment == "champion" and top.job_ids == ["c1", "c2"]
    assert top.your_submitted == 1 and top.your_hired == 1
    assert abs(top.hire_rate - 6 / 9) < 1e-9


# ─── digest ─────────────────────────────────────────────────────────────────


def test_digest_markdown_and_telegram(isolated_db, tmp_path, monkeypatch):
    import config
    import db
    from features.digest import build, send_telegram, write

    db.init_db()
    _seed_automation(db)
    db.upsert_jobs([{"id": "old", "published_at": _today(24 * 10), "budget_type": "HOURLY",
                     "budget_min": 20, "budget_max": 30, "budget_amount": 25}])  # fmt: skip
    monkeypatch.setattr(config, "EXPORTS_DIR", str(tmp_path))
    digest = build()
    assert digest.this_week.jobs == 4 and digest.last_week.jobs == 1
    assert "Jobs posted: **4**" in digest.text and "↑300%" in digest.text
    assert "Platforms:" in digest.text and "n8n 1 (new)" in digest.text
    assert "win rate" in digest.text and "n<5" in digest.text
    path = write(digest)
    assert path.endswith(".md") and pathlib.Path(path).read_text().startswith("# Upwork digest")

    calls = []

    class Resp:
        status_code = 200
        text = "ok"

    def poster(url, json=None, timeout=None):
        calls.append((url, json))
        return Resp()

    assert send_telegram("hi", token="", chat_id="", poster=poster) is False  # unconfigured
    assert send_telegram("hi", token="T", chat_id="42", poster=poster) is True
    assert calls[0][0].endswith("botT/sendMessage") and calls[0][1]["chat_id"] == "42"


# ─── service kinds ──────────────────────────────────────────────────────────


def test_digest_service_is_weekly_and_sync_is_periodic(tmp_path):
    from core import service

    sync = service.build_plist(python="/p", working_dir="/repo", logs=tmp_path, kind="sync")
    digest = service.build_plist(python="/p", working_dir="/repo", logs=tmp_path, kind="digest")
    assert sync["Label"] == "com.upwork-intel.sync" and "StartInterval" in sync
    assert digest["Label"] == "com.upwork-intel.digest"
    assert digest["StartCalendarInterval"] == {"Weekday": 1, "Hour": 8, "Minute": 0}
    assert "StartInterval" not in digest and "KeepAlive" not in digest
    assert digest["ProgramArguments"][-2:] == ["digest", "--send"]
    _, data = service.install(dry_run=True, kind="digest")
    assert plistlib.loads(data)["Label"] == "com.upwork-intel.digest"


# ─── dashboard on the Lens protocol ─────────────────────────────────────────


def test_dashboard_lens_conforms(isolated_db, monkeypatch):
    import config
    import db
    from core.lens import Lens, run_lens
    from features.dashboard.api import DashboardLens

    db.init_db()
    _seed_automation(db)
    monkeypatch.setattr(config, "EXPORTS_DIR", str(isolated_db.parent / "exports"))
    lens = DashboardLens(tz="UTC")
    assert isinstance(lens, Lens)
    report = run_lens(lens, 7)
    assert report["clients"]["total_jobs"] == 4
    assert (isolated_db.parent / "exports" / "latest.xlsx").exists()
