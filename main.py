import argparse
import sys
import time

from rich.console import Console
from rich.rule import Rule

from analyzer import client_stats, hourly_matrix, shift_recommendation, skills_stats
from config import TIMEZONE
from db import get_date_range, get_total_jobs, init_db
from display import (render_client_intelligence, render_shift_recommendation,
                     render_skills_heatmap, render_volume_heatmap)
from exporter import export_dashboard

console = Console()


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_fetch(args):
    from fetcher import fetch_jobs

    init_db()
    keywords = " ".join(args.keywords or [])
    categories = args.categories or [""]

    console.print(Rule("[bold cyan]Fetching from Upwork API[/bold cyan]"))

    total = 0
    for cat in categories:
        console.print(f"\n  [dim]Category:[/dim] [cyan]{cat or 'All'}[/cyan]")
        n = fetch_jobs(search_term=keywords, category=cat, limit=args.limit)
        total += n

    console.print(f"\n  [green]Done.[/green] {total} new jobs stored  ·  DB total: {get_total_jobs()}")

    # Auto-export after fetch so data is always captured
    _export(tz=TIMEZONE, days=14)

    console.print(f"  [dim]Run:[/dim]  python main.py dashboard\n")


def cmd_skills(args):
    init_db()
    _require_data()
    console.print(Rule("[bold cyan]Skills Demand Heatmap[/bold cyan]"))
    stats = skills_stats(days=args.days, categories=args.categories or None)
    render_skills_heatmap(stats, days=args.days, categories=args.categories)


def cmd_shift(args):
    init_db()
    _require_data()
    tz = args.timezone or TIMEZONE
    console.print(Rule(f"[bold cyan]Job Volume Heatmap  ·  {tz}[/bold cyan]"))
    matrix = hourly_matrix(tz_name=tz, days=args.days)
    render_volume_heatmap(matrix, tz_name=tz, days=args.days)
    rec = shift_recommendation(matrix)
    render_shift_recommendation(rec, tz_name=tz)


def cmd_dashboard(args):
    init_db()
    _require_data()
    tz = args.timezone or TIMEZONE

    while True:
        min_date, max_date = get_date_range()
        total = get_total_jobs()

        console.clear()
        console.print(Rule("[bold cyan]UPWORK JOB INTELLIGENCE DASHBOARD[/bold cyan]"))
        console.print(
            f"  [bright_black]{total:,} jobs  ·  "
            f"{(min_date or 'N/A')[:10]} → {(max_date or 'N/A')[:10]}  ·  "
            f"TZ: {tz}[/bright_black]\n"
        )

        stats = skills_stats(days=args.days, categories=args.categories or None)
        render_skills_heatmap(stats, days=args.days, categories=args.categories)

        cs = client_stats(days=args.days)
        render_client_intelligence(cs, days=args.days)

        matrix = hourly_matrix(tz_name=tz, days=args.days)
        render_volume_heatmap(matrix, tz_name=tz, days=args.days)

        rec = shift_recommendation(matrix)
        render_shift_recommendation(rec, tz_name=tz)

        _export(tz=tz, days=args.days, categories=args.categories)

        if not args.watch:
            break

        console.print(
            f"  [bright_black]Next refresh in {args.watch} min  ·  Ctrl+C to exit[/bright_black]\n"
        )
        try:
            time.sleep(args.watch * 60)
        except KeyboardInterrupt:
            console.print("\n[yellow]Exiting.[/yellow]\n")
            break


def cmd_n8n(args):
    import n8n_demand

    init_db()

    if not args.no_fetch:
        console.print(Rule("[bold cyan]Fetching n8n jobs from Upwork[/bold cyan]"))
        n = n8n_demand.fetch_n8n_jobs_from_api(days=args.days, limit=args.limit)
        console.print(f"  [green]Fetched / refreshed[/green] {n} jobs from API.\n")

    console.print(Rule(f"[bold cyan]n8n Demand · Last {args.days} days[/bold cyan]"))
    jobs = n8n_demand.get_n8n_jobs(days=args.days)
    report = n8n_demand.analyze(jobs)
    n8n_demand.render(report, days=args.days)

    try:
        path = n8n_demand.export(report, days=args.days)
        console.print(f"\n  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
    except Exception as exc:
        console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")


def cmd_seed(args):
    from seed import generate_demo_data

    init_db()
    console.print(Rule("[bold cyan]Generating Demo Data[/bold cyan]"))
    console.print(f"  Generating [cyan]{args.days}[/cyan] days of realistic job data...\n")
    count = generate_demo_data(days=args.days)
    console.print(f"  [green]Done.[/green] {count:,} demo jobs inserted into DB.\n")
    console.print("  [dim]Now run:[/dim]  python main.py dashboard\n")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _require_data():
    if get_total_jobs() == 0:
        console.print("[yellow]Database is empty.[/yellow]\n")
        console.print("  To use demo data:    [cyan]python main.py seed[/cyan]")
        console.print("  To fetch real data:  [cyan]python main.py fetch[/cyan]\n")
        sys.exit(0)


def _export(tz: str, days: int, categories: list = None):
    try:
        stats  = skills_stats(days=days, categories=categories or None)
        cs     = client_stats(days=days)
        matrix = hourly_matrix(tz_name=tz, days=days)
        rec    = shift_recommendation(matrix)
        path   = export_dashboard(stats, cs, matrix, rec, tz_name=tz, days=days)
        console.print(f"  [dim]Excel export:[/dim]  [cyan]{path}[/cyan]\n")
    except Exception as exc:
        console.print(f"  [yellow]Export skipped:[/yellow] {exc}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="upwork-intel",
        description="Upwork Job Intelligence — Skills Heatmap + BD Shift Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py seed                  # 14d of demo data (no API)\n"
            "  python main.py dashboard 7           # full dashboard, 7d window\n"
            "  python main.py dashboard 14 -t Asia/Kolkata -w 30\n"
            "  python main.py fetch -k python ai -l 1000\n"
            "  python main.py skills 7\n"
            "  python main.py shift 14 -t America/New_York\n"
            "  python main.py n8n 7                 # fetch + analyze last 7d of n8n jobs\n"
            "  python main.py n8n 14 -n             # skip fetch, analyze local DB only\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # seed
    p = sub.add_parser("seed", help="Generate realistic demo data (no API key needed)")
    p.add_argument("days", type=int, nargs="?", default=14,
                   help="Days of data to generate (default: 14)")

    # fetch
    p = sub.add_parser("fetch", help="Pull live jobs from Upwork GraphQL API")
    p.add_argument("-k", "--keywords", nargs="+", metavar="WORD", help="Search keywords")
    p.add_argument("-c", "--categories", nargs="+", metavar="CAT",
                   help="Category names to fetch separately")
    p.add_argument("-l", "--limit", type=int, default=500,
                   help="Max jobs per category (default: 500)")

    # skills
    p = sub.add_parser("skills", help="Show skills demand heatmap")
    p.add_argument("days", type=int, nargs="?", default=14,
                   help="Analysis window in days (default: 14)")
    p.add_argument("-c", "--categories", nargs="+", metavar="CAT",
                   help="Filter to specific categories")

    # shift
    p = sub.add_parser("shift", help="Show job volume heatmap + BD shift recommendation")
    p.add_argument("days", type=int, nargs="?", default=14,
                   help="Analysis window in days (default: 14)")
    p.add_argument("-t", "--timezone", type=str, metavar="TZ",
                   help="Timezone e.g. Asia/Kolkata")

    # n8n demand analysis
    p = sub.add_parser(
        "n8n",
        help="Analyze n8n automation demand by industry and workflow type",
    )
    p.add_argument("days", type=int, nargs="?", default=7,
                   help="Lookback window in days (default: 7)")
    p.add_argument("-n", "--no-fetch", action="store_true",
                   help="Skip the API fetch and only re-analyze the local DB")
    p.add_argument("-l", "--limit", type=int, default=1000,
                   help="Max jobs to pull from API (default: 1000)")

    # dashboard
    p = sub.add_parser("dashboard", help="Show full intelligence dashboard")
    p.add_argument("days", type=int, nargs="?", default=14,
                   help="Analysis window in days (default: 14)")
    p.add_argument("-c", "--categories", nargs="+", metavar="CAT",
                   help="Filter to specific categories")
    p.add_argument("-t", "--timezone", type=str, metavar="TZ",
                   help="Timezone e.g. Asia/Kolkata")
    p.add_argument("-w", "--watch", type=int, metavar="MIN",
                   help="Auto-refresh every N minutes")

    args = parser.parse_args()
    dispatch = {
        "seed": cmd_seed,
        "fetch": cmd_fetch,
        "skills": cmd_skills,
        "shift": cmd_shift,
        "dashboard": cmd_dashboard,
        "n8n": cmd_n8n,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
