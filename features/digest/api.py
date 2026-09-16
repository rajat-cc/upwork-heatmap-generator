"""Weekly digest.

Ten lines every Monday: this week versus last week for volume, platforms,
workflows and the hourly median, plus your funnel and the sync's health.
Written to `exports/digest_YYYY-Www.md`; sent to Telegram when a bot token and
chat id are configured (plain HTTP to the Bot API, no agent code involved).
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import requests

import config
from core.logging_setup import get_logger
from core.stats import median
from db import count_fetch_runs, get_conn, get_last_success, init_db
from features.funnel.analyzer import STAGES
from features.funnel.analyzer import analyze as analyze_funnel

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TOP_N = 6


@dataclass(slots=True)
class Window:
    label: str
    since: str
    until: str
    jobs: int = 0
    platforms: Counter = field(default_factory=Counter)
    workflows: Counter = field(default_factory=Counter)
    med_hourly: float = 0.0
    hourly_n: int = 0


@dataclass(slots=True)
class Digest:
    generated_at: str
    this_week: Window
    last_week: Window
    funnel_stages: dict[str, int]
    win_rate_shrunk: float
    win_insufficient: bool
    last_success: str | None
    runs_this_week: int
    text: str = ""


def _window(label: str, since: datetime, until: datetime) -> Window:
    s, u = since.strftime("%Y-%m-%dT%H:%M:%S"), until.strftime("%Y-%m-%dT%H:%M:%S")
    w = Window(label=label, since=s, until=u)
    with get_conn() as conn:
        w.jobs = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE published_at >= ? AND published_at < ?", (s, u)
        ).fetchone()[0]
        for axis, counter in (("platform", w.platforms), ("workflow", w.workflows)):
            for label_, n in conn.execute(
                """
                SELECT c.label, COUNT(DISTINCT c.job_id) FROM job_classifications c
                  JOIN jobs j ON j.id = c.job_id
                 WHERE c.axis = ? AND j.published_at >= ? AND j.published_at < ?
                 GROUP BY c.label
                """,
                (axis, s, u),
            ):
                counter[label_] = n
        hourly = [
            (r[0] + r[1]) / 2 if r[0] and r[1] else r[2]
            for r in conn.execute(
                "SELECT budget_min, budget_max, budget_amount FROM jobs "
                "WHERE budget_type = 'HOURLY' AND budget_amount > 0 "
                "AND published_at >= ? AND published_at < ?",
                (s, u),
            )
        ]
    w.med_hourly = median(hourly)
    w.hourly_n = len(hourly)
    return w


def _delta(now: int | float, before: int | float) -> str:
    if before == 0:
        return "new" if now else "—"
    pct = (now - before) / before * 100
    arrow = "↑" if pct > 0 else "↓" if pct < 0 else "→"
    return f"{arrow}{abs(pct):.0f}%"


def build(now: datetime | None = None) -> Digest:
    now = now or datetime.now(UTC)
    this = _window("this week", now - timedelta(days=7), now)
    last = _window("last week", now - timedelta(days=14), now - timedelta(days=7))
    funnel = analyze_funnel(7)
    digest = Digest(
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        this_week=this,
        last_week=last,
        funnel_stages=funnel.stages,
        win_rate_shrunk=funnel.win.shrunk,
        win_insufficient=funnel.win.insufficient,
        last_success=get_last_success(),
        runs_this_week=count_fetch_runs(since_iso=this.since),
    )
    digest.text = render_markdown(digest)
    return digest


def render_markdown(d: Digest) -> str:
    t, lw = d.this_week, d.last_week
    week = datetime.strptime(t.until[:10], "%Y-%m-%d").isocalendar()
    lines = [f"# Upwork digest · week {week.year}-W{week.week:02d}", ""]
    lines.append(f"- Jobs posted: **{t.jobs:,}** ({_delta(t.jobs, lw.jobs)} vs last week)")
    if t.hourly_n:
        lines.append(
            f"- Median hourly budget: **${t.med_hourly:.0f}/hr** ({_delta(t.med_hourly, lw.med_hourly)}, n={t.hourly_n})"
        )
    for title, now_c, before_c in (
        ("Platforms", t.platforms, lw.platforms),
        ("Workflows", t.workflows, lw.workflows),
    ):
        if now_c:
            parts = [
                f"{name} {n} ({_delta(n, before_c.get(name, 0))})"
                for name, n in now_c.most_common(TOP_N)
            ]
            lines.append(f"- {title}: " + ", ".join(parts))
    stages = " → ".join(
        f"{s.lower()} {d.funnel_stages.get(s, 0)}"
        for s in STAGES
        if d.funnel_stages.get(s, 0) or s in ("NOTIFIED", "SUBMITTED", "HIRED")
    )
    flag = " (n<5)" if d.win_insufficient else ""
    lines.append(f"- Funnel (7d): {stages}; win rate {d.win_rate_shrunk * 100:.0f}%{flag}")
    last = (d.last_success or "never").replace("T", " ")[:16]
    lines.append(f"- Sync: {d.runs_this_week} fetch runs this week; last success {last}")
    lines.append("")
    lines.append(f"_generated {d.generated_at[:16].replace('T', ' ')}Z · upwork-intel_")
    return "\n".join(lines)


def write(digest: Digest, path: str | None = None) -> str:
    os.makedirs(config.EXPORTS_DIR, exist_ok=True)
    if path is None:
        week = datetime.strptime(digest.this_week.until[:10], "%Y-%m-%d").isocalendar()
        path = os.path.join(config.EXPORTS_DIR, f"digest_{week.year}-W{week.week:02d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(digest.text + "\n")
    return path


def send_telegram(
    text: str,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    poster: Callable = requests.post,
) -> bool:
    token = token or config.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    resp = poster(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    ok = getattr(resp, "status_code", 500) == 200
    if not ok:
        log.warning("Telegram send failed: %s", getattr(resp, "text", "")[:200])
    return ok


def run(*, send: bool = False, path: str | None = None) -> tuple[str, bool]:
    init_db()
    digest = build()
    out = write(digest, path)
    sent = send_telegram(digest.text) if send else False
    return out, sent
