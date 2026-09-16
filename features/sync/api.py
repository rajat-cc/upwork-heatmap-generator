"""The `sync` pipeline.

Runs from the LaunchAgent every two hours (`core/service.py`) or by hand
(`make sync`):

  1. fetch every watch (`core/watches.py`), recording a `fetch_runs` row each
  2. classify the rows that fetch touched (all four regex axes) into the cache
  3. snapshots: `search`-stage observations are written inside `upsert_jobs`;
     detail stages stay gated on `docs/api_probe.json`
  4. blank fetched text older than the retention limit
  5. write `exports/status.json` for the staleness badge and the digest

A lock file prevents overlapping runs. An auth failure stops the run early
and exits 2 so the operator sees "re-authorize" instead of a retry storm.
`--offline` replays a recorded page so CI can exercise the whole path.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich import box
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

import config
from core.logging_setup import get_logger
from core.rich_helpers import data_as_of_line
from core.watches import Watch, load_watches
from db import _now_iso, get_last_success, get_total_jobs, init_db, jobs_fetched_since, purge_text
from features.n8n import classifier
from fetcher import FixtureTransport, GraphQLError, TransientError, fetch_jobs

console = Console()
log = get_logger(__name__)

LOCK_FILE = ".sync.lock"
STATUS_FILE = "status.json"
OFFLINE_FIXTURE = Path(config.ROOT_DIR) / "tests" / "fixtures" / "graphql" / "sync_offline.json"


@dataclass
class WatchResult:
    label: str
    summary: str
    jobs: int = 0
    status: str = "done"
    error: str | None = None


@dataclass
class SyncResult:
    started_at: str
    finished_at: str = ""
    offline: bool = False
    watches_source: str = ""
    watches: list[WatchResult] = field(default_factory=list)
    classified_jobs: int = 0
    classification_rows: int = 0
    detail_snapshots: str = "gated: run `make probe` first"
    purged: int = 0
    jobs_total: int = 0
    last_success_at: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(w.status == "done" for w in self.watches)

    def exit_code(self) -> int:
        if self.error == "reauth":
            return 2
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def run(
    *,
    offline: bool = False,
    snapshots: bool = True,
    purge: bool = True,
    since_days: int | None = None,
    limit: int | None = None,
    transport=None,
    quiet: bool = False,
) -> SyncResult:
    init_db()
    since_days = config.SYNC_SINCE_DAYS if since_days is None else since_days
    limit = config.SYNC_LIMIT_PER_WATCH if limit is None else limit
    result = SyncResult(started_at=_now_iso(), offline=offline)
    if not snapshots:
        result.detail_snapshots = "disabled (--no-snapshots)"

    lock = _acquire_lock()
    if lock is None:
        result.error = "locked"
        result.finished_at = _now_iso()
        _write_status(result)
        if not quiet:
            console.print("[yellow]Another sync is running (lock held); exiting.[/yellow]")
        return result

    try:
        if offline:
            transport = transport or FixtureTransport(json.loads(OFFLINE_FIXTURE.read_text()))
            watches: list[Watch] = [Watch(label="offline-n8n", search_expression="n8n")]
            source, token = "offline fixture", "offline"
        else:
            watches, source = load_watches()
            token = None
        result.watches_source = source

        if not quiet:
            console.print(Rule("[bold cyan]Sync[/bold cyan]"))
            console.print(f"  [dim]watches:[/dim] {len(watches)} from {source}")

        for w in watches:
            wr = WatchResult(label=w.label, summary=w.summary())
            if not quiet:
                console.print(f"\n  [bold]{w.label}[/bold]  [dim]{w.summary()}[/dim]")
            try:
                wr.jobs = fetch_jobs(
                    search_term=w.search_expression or "",
                    category_ids=w.category_ids,
                    subcategory_ids=w.subcategory_ids,
                    skill_ids=w.skill_ids,
                    limit=limit,
                    since_days=since_days,
                    label=w.label,
                    transport=transport,
                    token=token,
                )
            except (GraphQLError, TransientError) as exc:
                wr.status, wr.error = "error", str(exc)
                log.error("Watch %r failed: %s", w.label, exc)
            except RuntimeError as exc:
                # Auth failure: the token cache is gone; nothing else can succeed.
                wr.status, wr.error = "error", str(exc)
                result.error = "reauth"
                result.watches.append(wr)
                log.error("Sync stopped: %s", exc)
                break
            result.watches.append(wr)

        touched = jobs_fetched_since(result.started_at)
        jobs = classifier.classify_rows(touched)
        result.classified_jobs = len(jobs)
        result.classification_rows = classifier.persist(jobs)

        if purge:
            result.purged = purge_text(config.PURGE_TEXT_HOURS, config.PURGE_FIELDS)
    finally:
        _release_lock(lock)

    result.jobs_total = get_total_jobs()
    result.last_success_at = get_last_success()
    result.finished_at = _now_iso()
    _write_status(result)
    if not quiet:
        _render(result)
    return result


# ─── Helpers ────────────────────────────────────────────────────────────────


def _lock_path() -> str:
    return os.path.join(config.ROOT_DIR, LOCK_FILE)


def _acquire_lock():
    fh = open(_lock_path(), "w")  # noqa: SIM115 - held for the run, released in finally
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(f"{os.getpid()} {_now_iso()}\n")
    fh.flush()
    return fh


def _release_lock(fh) -> None:
    if fh is None:
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def status_path() -> str:
    return os.path.join(config.EXPORTS_DIR, STATUS_FILE)


def _write_status(result: SyncResult) -> None:
    os.makedirs(config.EXPORTS_DIR, exist_ok=True)
    target = status_path()
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result.to_dict(), f, indent=2, sort_keys=True)
    os.replace(tmp, target)


def _render(result: SyncResult) -> None:
    table = Table(
        title="Sync result",
        box=box.ROUNDED,
        title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column("Watch", style="bold white")
    table.add_column("Jobs", justify="right", style="cyan")
    table.add_column("Status")
    for w in result.watches:
        status = "[green]done[/green]" if w.status == "done" else f"[red]{w.error}[/red]"
        table.add_row(w.label, str(w.jobs), status)
    console.print()
    console.print(table)
    console.print(
        f"  classified {result.classified_jobs} jobs ({result.classification_rows} labels)  ·  "
        f"purged text on {result.purged} rows  ·  {result.jobs_total:,} jobs in DB\n"
        f"  {data_as_of_line(result.last_success_at)}\n"
        f"  [dim]detail snapshots: {result.detail_snapshots}  ·  status: {status_path()}[/dim]\n"
    )
    if result.error == "reauth":
        console.print("[red]Authorization failed. Run:  make auth[/red]\n")
