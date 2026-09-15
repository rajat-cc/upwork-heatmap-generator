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
    }
    try:
        dispatch[args.command](args)
    except RuntimeError as exc:
        # Auth and configuration failures carry a user-facing message; no traceback.
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
