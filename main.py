"""CLI entry point.

Thin dispatcher — all real work lives in `features/*` packages.
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.rule import Rule

from config import TIMEZONE
from core.logging_setup import get_logger
from db import get_total_jobs, init_db
from features.dashboard import run as run_dashboard
from features.dashboard import run_shift_only, run_skills_only
from features.n8n import run as run_n8n

console = Console()
log = get_logger(__name__)


# ─── Subcommand handlers ────────────────────────────────────────────────────


def cmd_seed(args: argparse.Namespace) -> None:
    from seed import generate_demo_data

    init_db()
    console.print(Rule("[bold cyan]Generating Demo Data[/bold cyan]"))
    console.print(f"  Generating [cyan]{args.days}[/cyan] days of realistic job data...\n")
    count = generate_demo_data(days=args.days)
    console.print(f"  [green]Done.[/green] {count:,} demo jobs inserted into DB.\n")
    console.print("  [dim]Now run:[/dim]  python main.py dashboard\n")


def cmd_fetch(args: argparse.Namespace) -> None:
    from fetcher import fetch_jobs

    init_db()
    keywords = " ".join(args.keywords or [])

    console.print(Rule("[bold cyan]Fetching from Upwork API[/bold cyan]"))
    console.print(
        f"  [dim]Search:[/dim] [cyan]{keywords or '(all)'}[/cyan]"
        + (f"  [dim]categories:[/dim] {','.join(args.category_ids)}" if args.category_ids else "")
        + (
            f"  [dim]subcategories:[/dim] {','.join(args.subcategory_ids)}"
            if args.subcategory_ids
            else ""
        )
    )

    total = fetch_jobs(
        search_term=keywords,
        category_ids=args.category_ids,
        subcategory_ids=args.subcategory_ids,
        skill_ids=args.skill_ids,
        limit=args.limit,
        since_days=args.since_days,
    )

    console.print(f"\n  [green]Done.[/green] {total} jobs touched  ·  DB total: {get_total_jobs()}")
    console.print("  [dim]Run:[/dim]  python main.py dashboard\n")


def cmd_probe(args: argparse.Namespace) -> None:
    from core.probe import run_probe

    console.print(Rule("[bold cyan]API capability probe[/bold cyan]"))
    run_probe(offline=args.offline, out=args.out)


def cmd_ingest(args: argparse.Namespace) -> None:
    from features.funnel.api import run_ingest

    run_ingest(args.agent_dir)


def cmd_outcome(args: argparse.Namespace) -> None:
    from features.funnel.outcomes import outcomes_from_csv, record_outcome, record_outcomes

    init_db()
    if args.from_csv:
        events = outcomes_from_csv(args.from_csv)
        n = record_outcomes(events)
        console.print(f"  [green]Recorded[/green] {n} new outcome(s) from {len(events)} rows.\n")
        return
    if not (args.job and args.event):
        raise SystemExit(
            "usage: main.py outcome <job id|url> <submitted|viewed|interview|hired|lost> [...]"
        )
    try:
        event, n = record_outcome(
            args.job,
            args.event,
            bid_amount=args.bid,
            bid_type=args.bid_type,
            connects=args.connects,
            note=args.note or "",
            ts=args.ts,
        )
    except ValueError as exc:
        raise SystemExit(f"  {exc}") from exc
    state = "recorded" if n else "already recorded"
    console.print(
        f"  [green]{event['event']}[/green] {state} for job [cyan]{event['job_id']}[/cyan] at {event['ts']}"
        + (
            f"  ·  bid ${event['bid_amount']:g} {event['bid_type'] or ''}"
            if event["bid_amount"]
            else ""
        )
        + (f"  ·  {event['connects_spent']} connects" if event["connects_spent"] else "")
        + "\n"
    )


def cmd_outcomes(args: argparse.Namespace) -> None:
    from db import recent_events

    init_db()
    rows = recent_events(args.days)
    if not rows:
        console.print(f"  No manual outcomes in the last {args.days} days.\n")
        return
    for r in rows:
        bid = f"  ${r['bid_amount']:g} {r['bid_type'] or ''}" if r["bid_amount"] else ""
        console.print(
            f"  {r['ts'][:16]}  [bold]{r['event']:<9}[/bold] {r['job_id']}{bid}  [dim]{r['source']}[/dim]"
        )
    console.print()


def cmd_funnel(args: argparse.Namespace) -> None:
    from features.funnel import run as run_funnel

    run_funnel(days=args.days, export=not args.no_export)


def cmd_sync(args: argparse.Namespace) -> None:
    from features.sync import run as run_sync

    result = run_sync(
        offline=args.offline,
        snapshots=not args.no_snapshots,
        purge=not args.no_purge,
        since_days=args.since_days,
        limit=args.limit,
    )
    raise SystemExit(result.exit_code())


def cmd_purge(args: argparse.Namespace) -> None:
    from config import PURGE_FIELDS, PURGE_TEXT_HOURS
    from db import purge_text

    init_db()
    hours = PURGE_TEXT_HOURS if args.hours is None else args.hours
    n = purge_text(hours, PURGE_FIELDS, dry_run=args.dry_run)
    verb = "would blank" if args.dry_run else "blanked"
    console.print(
        f"  {verb} {', '.join(PURGE_FIELDS)} on [cyan]{n}[/cyan] job(s) last fetched "
        f"more than {hours} h ago" + ("  [dim](dry run)[/dim]\n" if args.dry_run else "\n")
    )


def cmd_install_service(args: argparse.Namespace) -> None:
    from core.service import LABEL, install

    path, data = install(interval=args.interval, dry_run=args.dry_run)
    if args.dry_run:
        console.print(data.decode())
        console.print(f"  [dim]dry run — would write[/dim] {path}\n")
        return
    console.print(
        f"  [green]Installed[/green] {LABEL}: sync every {args.interval // 60} min\n"
        f"  plist: {path}\n  logs:  ~/Library/Logs/upwork-intel/\n"
        f"  [dim]check:[/dim] launchctl list | grep upwork-intel\n"
    )


def cmd_uninstall_service(args: argparse.Namespace) -> None:
    from core.service import LABEL, uninstall

    removed = uninstall()
    console.print(f"  {'Removed' if removed else 'Not installed:'} {LABEL}\n")


def cmd_dashboard(args: argparse.Namespace) -> None:
    run_dashboard(
        days=args.days,
        tz=args.timezone or TIMEZONE,
        categories=args.categories,
        watch=args.watch,
    )


def cmd_skills(args: argparse.Namespace) -> None:
    run_skills_only(days=args.days, categories=args.categories)


def cmd_shift(args: argparse.Namespace) -> None:
    run_shift_only(days=args.days, tz=args.timezone or TIMEZONE)


def cmd_n8n(args: argparse.Namespace) -> None:
    run_n8n(days=args.days, fetch=not args.no_fetch, limit=args.limit)


# ─── CLI ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upwork-intel",
        description="Upwork Job Intelligence — Skills Heatmap + BD Shift Optimizer + n8n Demand",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py seed                  # 14d of demo data (no API)\n"
            "  python main.py dashboard 7           # full dashboard, 7d window\n"
            "  python main.py dashboard 14 -t Asia/Kolkata -w 30\n"
            "  python main.py fetch -k python ai -l 1000\n"
            "  python main.py probe                 # which API queries/fields this key can use\n"
            "  python main.py sync                  # fetch watches + classify + purge (launchd runs this)\n"
            "  python main.py install-service       # run sync every 2 hours via launchd\n"
            "  python main.py ingest                # read the proposal agent's logs into the ledger\n"
            "  python main.py outcome <job> hired --bid 45 --bid-type hourly --connects 16\n"
            "  python main.py funnel 30             # win rate, cost per hire, by segment\n"
            "  python main.py skills 7\n"
            "  python main.py shift 14 -t America/New_York\n"
            "  python main.py n8n 7                 # fetch + analyze last 7d of n8n jobs\n"
            "  python main.py n8n 14 -n             # skip fetch, analyze local DB only\n"
            "\n"
            "Note: `n8n` fetches from the API by default — every other analysis reads only.\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="Generate realistic demo data (no API key needed)")
    p.add_argument(
        "days", type=int, nargs="?", default=14, help="Days of data to generate (default: 14)"
    )

    p = sub.add_parser("fetch", help="Pull live jobs from Upwork GraphQL API")
    p.add_argument("-k", "--keywords", nargs="+", metavar="WORD", help="Search keywords")
    p.add_argument(
        "-c",
        "--category-ids",
        nargs="+",
        metavar="UID",
        help="Upwork category UIDs to filter on (server-side, from a pasted search URL)",
    )
    p.add_argument(
        "-s",
        "--subcategory-ids",
        nargs="+",
        metavar="UID",
        help="Upwork subcategory UIDs to filter on (server-side)",
    )
    p.add_argument(
        "--skill-ids", nargs="+", metavar="UID", help="Ontology skill UIDs (job must match ALL)"
    )
    p.add_argument("-l", "--limit", type=int, default=500, help="Max jobs to pull (default: 500)")
    p.add_argument(
        "--since-days",
        type=int,
        metavar="N",
        help="Stop paging once results are older than N days",
    )

    p = sub.add_parser("probe", help="Probe which API fields/queries this key can use")
    p.add_argument(
        "--offline",
        action="store_true",
        help="Replay the recorded fixture instead of calling the API (CI smoke)",
    )
    p.add_argument(
        "--out",
        metavar="PATH",
        help="Where to write the JSON report (default: docs/api_probe.json)",
    )

    p = sub.add_parser("sync", help="Fetch watches, classify, snapshot, purge, write status.json")
    p.add_argument("--offline", action="store_true", help="Replay the recorded page (CI smoke)")
    p.add_argument("--no-snapshots", action="store_true", help="Skip detail-stage snapshots")
    p.add_argument("--no-purge", action="store_true", help="Skip the retention purge")
    p.add_argument("--since-days", type=int, metavar="N", help="Lookback per watch (default 2)")
    p.add_argument("-l", "--limit", type=int, metavar="N", help="Max jobs per watch (default 300)")

    p = sub.add_parser(
        "ingest", help="Read the proposal agent's alerts/drafts into the outcome ledger"
    )
    p.add_argument("--agent-dir", metavar="DIR", help="Override UPWORK_AGENT_DIR")

    p = sub.add_parser(
        "outcome", help="Record a proposal outcome (submitted/viewed/interview/hired/lost)"
    )
    p.add_argument("job", nargs="?", help="Job id, job URL or ~ciphertext")
    p.add_argument("event", nargs="?", help="submitted | viewed | interview | hired | lost")
    p.add_argument("--bid", type=float, metavar="USD", help="Bid amount")
    p.add_argument("--bid-type", choices=["hourly", "fixed"], help="Bid type")
    p.add_argument("--connects", type=int, metavar="N", help="Connects spent on this proposal")
    p.add_argument("--note", metavar="TEXT", help="Free-text note")
    p.add_argument("--ts", metavar="ISO", help="Event time (default: now)")
    p.add_argument(
        "--from-csv",
        metavar="PATH",
        help="Bulk file: job,event[,ts,bid_amount,bid_type,connects,note]",
    )

    p = sub.add_parser("outcomes", help="List recently recorded outcomes")
    p.add_argument("--days", type=int, default=30, help="Lookback (default 30)")

    p = sub.add_parser("funnel", help="Proposal funnel: stages, win rate, cost per hire, segments")
    p.add_argument("days", type=int, nargs="?", default=30, help="Window in days (default: 30)")
    p.add_argument("--no-export", action="store_true", help="Skip the Excel export")

    p = sub.add_parser("purge", help="Blank fetched text older than the retention limit")
    p.add_argument("--dry-run", action="store_true", help="Count only; change nothing")
    p.add_argument("--hours", type=int, metavar="H", help="Override UPWORK_PURGE_TEXT_HOURS")

    p = sub.add_parser("install-service", help="Install the launchd job that runs sync")
    p.add_argument("--interval", type=int, default=7200, metavar="SECONDS", help="Default 7200")
    p.add_argument("--dry-run", action="store_true", help="Print the plist without installing")

    sub.add_parser("uninstall-service", help="Remove the launchd sync job")

    p = sub.add_parser("skills", help="Show skills demand heatmap")
    p.add_argument(
        "days", type=int, nargs="?", default=14, help="Analysis window in days (default: 14)"
    )
    p.add_argument(
        "-c", "--categories", nargs="+", metavar="CAT", help="Filter to specific categories"
    )

    p = sub.add_parser("shift", help="Show job volume heatmap + BD shift recommendation")
    p.add_argument(
        "days", type=int, nargs="?", default=14, help="Analysis window in days (default: 14)"
    )
    p.add_argument("-t", "--timezone", type=str, metavar="TZ", help="Timezone e.g. Asia/Kolkata")

    p = sub.add_parser(
        "n8n",
        help="Analyze n8n automation demand by industry and workflow type",
    )
    p.add_argument(
        "days", type=int, nargs="?", default=7, help="Lookback window in days (default: 7)"
    )
    p.add_argument(
        "-n",
        "--no-fetch",
        action="store_true",
        help="Skip the API fetch and only re-analyze the local DB",
    )
    p.add_argument(
        "-l", "--limit", type=int, default=1000, help="Max jobs to pull from API (default: 1000)"
    )

    p = sub.add_parser("dashboard", help="Show full intelligence dashboard")
    p.add_argument(
        "days", type=int, nargs="?", default=14, help="Analysis window in days (default: 14)"
    )
    p.add_argument(
        "-c", "--categories", nargs="+", metavar="CAT", help="Filter to specific categories"
    )
    p.add_argument("-t", "--timezone", type=str, metavar="TZ", help="Timezone e.g. Asia/Kolkata")
    p.add_argument("-w", "--watch", type=int, metavar="MIN", help="Auto-refresh every N minutes")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    dispatch = {
        "seed": cmd_seed,
        "fetch": cmd_fetch,
        "skills": cmd_skills,
        "shift": cmd_shift,
        "dashboard": cmd_dashboard,
        "n8n": cmd_n8n,
        "probe": cmd_probe,
        "sync": cmd_sync,
        "ingest": cmd_ingest,
        "outcome": cmd_outcome,
        "outcomes": cmd_outcomes,
        "funnel": cmd_funnel,
        "purge": cmd_purge,
        "install-service": cmd_install_service,
        "uninstall-service": cmd_uninstall_service,
    }
    try:
        dispatch[args.command](args)
    except RuntimeError as exc:
        # Auth and configuration failures carry a user-facing message; no traceback.
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
