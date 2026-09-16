"""Client accounts from a heuristic fingerprint.

The search API exposes no client id, so jobs are grouped by
(country, total spent, total hires, jobs posted): a client's history changes
slowly, so identical tuples inside one window almost always mean one client.
A fingerprint of all zeros carries no identity and is skipped. Every row is
labelled as heuristic; the `clients` table lands when the live probe finds a
stable identity field.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.models import Job
from core.scoring import client_segment_of
from db import events_since, get_conn, load_classifications
from taxonomies.countries import iso2_to_name


@dataclass(slots=True)
class ClientRow:
    key: str
    country: str
    country_name: str
    segment: str
    spent: float
    hires: int
    posted: int
    hire_rate: float | None
    posts: int
    first_seen: str
    last_seen: str
    workflows: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    your_submitted: int = 0
    your_hired: int = 0
    job_ids: list[str] = field(default_factory=list)

    @property
    def likely_repeat(self) -> bool:
        return self.posts >= 2


@dataclass(slots=True)
class ClientsReport:
    days: int
    since: str
    jobs: int
    rows: list[ClientRow]

    @property
    def repeat(self) -> list[ClientRow]:
        return [r for r in self.rows if r.likely_repeat]


def fingerprint(job: Job) -> str | None:
    if job.client_total_spent <= 0 and job.client_total_hires <= 0 and job.client_total_posted <= 1:
        return None
    raw = f"{job.client_country}|{round(job.client_total_spent)}|{job.client_total_hires}|{job.client_total_posted}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def analyze(days: int = 90) -> ClientsReport:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE published_at >= ?", (since,)).fetchall()
    jobs = [Job.from_row(r) for r in rows]
    labels = load_classifications([j.id for j in jobs]) if jobs else {}

    submitted: dict[str, int] = defaultdict(int)
    hired: dict[str, int] = defaultdict(int)
    for e in events_since(since + "Z"):
        if e["event"] == "SUBMITTED":
            submitted[e["job_id"]] += 1
        elif e["event"] == "HIRED":
            hired[e["job_id"]] += 1

    groups: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        key = fingerprint(job)
        if key:
            groups[key].append(job)

    out: list[ClientRow] = []
    for key, members in groups.items():
        members.sort(key=lambda j: j.published_at)
        head = members[-1]
        wfs: dict[str, int] = defaultdict(int)
        pls: dict[str, int] = defaultdict(int)
        for j in members:
            for w in labels.get(j.id, {}).get("workflow", []):
                wfs[w] += 1
            for p in labels.get(j.id, {}).get("platform", []):
                pls[p] += 1
        out.append(
            ClientRow(
                key=key,
                country=head.client_country,
                country_name=iso2_to_name(head.client_country) or "Unknown",
                segment=client_segment_of(head),
                spent=head.client_total_spent,
                hires=head.client_total_hires,
                posted=head.client_total_posted,
                hire_rate=head.hire_rate,
                posts=len(members),
                first_seen=members[0].published_at,
                last_seen=head.published_at,
                workflows=[w for w, _ in sorted(wfs.items(), key=lambda kv: -kv[1])][:3],
                platforms=[p for p, _ in sorted(pls.items(), key=lambda kv: -kv[1])][:3],
                your_submitted=sum(submitted.get(j.id, 0) for j in members),
                your_hired=sum(hired.get(j.id, 0) for j in members),
                job_ids=[j.id for j in members],
            )
        )
    out.sort(key=lambda r: (r.posts, r.spent), reverse=True)
    return ClientsReport(days=days, since=since, jobs=len(jobs), rows=out)
