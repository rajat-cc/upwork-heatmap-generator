"""Detail-stage snapshots: +2h / +24h / +72h observations via `marketplaceJobPosting(id)`.

Gated on the live probe (`core.capabilities.detail_snapshots_reason`): the
detail query is scope-gated on some keys and only worth calling when the
type carries `activityStat`. Each run takes at most
`config.SNAPSHOT_DETAIL_CAP` observations, later stages first (see
`db.due_detail_snapshots`). A detail row records hires so far (`filled` =
hired_count > 0), invites, interviews, offers, the posting's open/closed
state, the bid statistics Upwork exposes, and the client's public company id,
the stable identity the search node lacks. A posting the API no longer
returns is recorded as `MISSING` so it is not asked again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import auth
import config
from core import capabilities
from core.logging_setup import get_logger
from db import due_detail_snapshots, record_detail_snapshot
from fetcher import GraphQLError, TransientError, Transport, fetch_job_detail, get_org_id

log = get_logger(__name__)


@dataclass(slots=True)
class SnapshotRun:
    due: int = 0
    taken: int = 0
    missing: int = 0
    errors: int = 0
    clients_identified: int = 0
    skipped: str | None = None

    def summary(self) -> str:
        if self.skipped and not (self.taken or self.missing):
            return self.skipped
        text = f"{self.taken} taken of {self.due} due"
        if self.missing:
            text += f", {self.missing} gone"
        if self.errors:
            text += f", {self.errors} errors"
        if self.clients_identified:
            text += f", {self.clients_identified} client ids"
        if self.skipped:
            text += f" ({self.skipped})"
        return text


def take_detail_snapshots(
    *,
    limit: int | None = None,
    transport: Transport | None = None,
    token: str | None = None,
    probe_path=None,
    now: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SnapshotRun:
    run = SnapshotRun()
    reason = capabilities.detail_snapshots_reason(probe_path)
    if reason:
        run.skipped = reason
        return run
    cap = config.SNAPSHOT_DETAIL_CAP if limit is None else limit
    due = due_detail_snapshots(now=now)
    run.due = len(due)
    if not due:
        return run

    token = token or auth.get_access_token()
    org_id = get_org_id(transport=transport, token=token)
    for job_id, stage in due[:cap]:
        try:
            detail = fetch_job_detail(
                job_id, transport=transport, token=token, org_id=org_id, sleep=sleep
            )
        except GraphQLError as exc:
            if exc.is_permission_error:
                run.skipped = f"stopped: {exc}"
                break
            run.errors += 1
            log.warning("Detail snapshot %s %s failed: %s", job_id, stage, exc)
            continue
        except TransientError as exc:
            run.errors += 1
            log.warning("Detail snapshot %s %s failed: %s", job_id, stage, exc)
            continue
        if detail is None:
            record_detail_snapshot(job_id, stage, {"status": "MISSING"}, observed_at=None)
            run.missing += 1
            continue
        record_detail_snapshot(job_id, stage, detail)
        run.taken += 1
        if detail.get("client_company_id"):
            run.clients_identified += 1
    return run
