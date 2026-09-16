from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.rich_helpers import fmt_band

console = Console()

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ─── Skills Heatmap ──────────────────────────────────────────────────────────


def render_skills_heatmap(stats: list, days: int, categories: list = None):
    if not stats:
        console.print("[yellow]No skill data found. Run:  make sync[/yellow]")
        return

    cat_label = f"  [{', '.join(categories)}]" if categories else ""
    table = Table(
        title=f"SKILLS DEMAND HEATMAP  ·  Last {days} days{cat_label}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on grey23",
        title_style="bold cyan",
        border_style="bright_black",
        pad_edge=False,
        show_lines=False,
        expand=True,
    )

    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Skill", style="bold white", min_width=16, ratio=1)
    table.add_column("Jobs", justify="right", style="cyan", min_width=5)
    table.add_column("Demand", min_width=10)
    table.add_column("Trend", justify="right", min_width=7)
    table.add_column("Opp", justify="right", min_width=4)
    table.add_column("Compete", justify="right", min_width=8)
    table.add_column("Med $/hr", justify="right", style="green", min_width=14)
    table.add_column("Med fixed", justify="right", style="yellow", min_width=14)
    table.add_column("%H", justify="right", min_width=4)
    table.add_column("Seniority", min_width=24)

    max_count = stats[0]["count"] if stats else 1
    trend_reasons = {row.get("trend_reason") for row in stats}

    for i, row in enumerate(stats, 1):
        # Trend
        trend_pct = row["trend_pct"]
        if trend_pct is None:
            trend_str = "[bright_black]   n/a[/bright_black]"
        elif trend_pct >= 20:
            trend_str = f"[bold green]↑ {trend_pct}%[/bold green]"
        elif trend_pct > 0:
            trend_str = f"[green]↑ {trend_pct}%[/green]"
        elif trend_pct < -20:
            trend_str = f"[bold red]↓ {abs(trend_pct)}%[/bold red]"
        elif trend_pct < 0:
            trend_str = f"[red]↓ {abs(trend_pct)}%[/red]"
        else:
            trend_str = "[yellow]→  0%[/yellow]"

        # Opportunity score with colour
        opp = row["opportunity_score"]
        if opp >= 70:
            opp_str = f"[bold green]{opp}[/bold green]"
        elif opp >= 45:
            opp_str = f"[yellow]{opp}[/yellow]"
        else:
            opp_str = f"[bright_black]{opp}[/bright_black]"

        # Competition (median proposals per job)
        props = row["med_proposals"]
        if props == 0:
            comp_str = "[bright_black]—[/bright_black]"
        elif props <= 5:
            comp_str = f"[bold green]{props:.0f}[/bold green]"
        elif props <= 15:
            comp_str = f"[yellow]{props:.0f}[/yellow]"
        else:
            comp_str = f"[red]{props:.0f}[/red]"

        hourly_str = fmt_band(row["hourly_p25"], row["med_hourly"], row["hourly_p75"], "/hr")
        fixed_str = fmt_band(row["fixed_p25"], row["med_fixed"], row["fixed_p75"])

        # % Hourly column with colour
        hp = row["hourly_pct"]
        if hp >= 60:
            hp_str = f"[green]{hp}%[/green]"
        elif hp >= 40:
            hp_str = f"[yellow]{hp}%[/yellow]"
        else:
            hp_str = f"[bright_black]{hp}%[/bright_black]"

        bar = _demand_bar(row["count"], max_count)
        tier = _tier_bar(row["entry_pct"], row["mid_pct"], row["exp_pct"])
        row_style = "on grey7" if i % 2 == 0 else ""

        table.add_row(
            str(i),
            row["skill"],
            f"{row['count']:,}",
            bar,
            trend_str,
            opp_str,
            comp_str,
            hourly_str,
            fixed_str,
            hp_str,
            tier,
            style=row_style,
        )

    console.print()
    console.print(table)
    if "runs" in trend_reasons:
        trend_note = "Trend n/a: fewer than 3 fetch runs in this window"
    else:
        trend_note = "Trend = 2nd half vs 1st half (promptly discovered jobs only, ≥5 per half)"
    console.print(
        f"  [bright_black]{trend_note}  ·  "
        "Opp = demand×budget÷competition (0–100)  ·  "
        "Compete = median proposals/job  ·  "
        "$ = median (p25–p75)  ·  "
        "Seniority:[/bright_black]  "
        "[bright_black]▪[/bright_black] Entry  "
        "[cyan]▪[/cyan] Mid  "
        "[yellow]▪[/yellow] Expert"
    )
    console.print()


# ─── Client Intelligence ─────────────────────────────────────────────────────


def render_client_intelligence(cs: dict, days: int):
    if not cs or cs.get("total_jobs", 0) == 0:
        return

    console.print()
    console.rule("[bold cyan]CLIENT INTELLIGENCE[/bold cyan]")
    console.print()

    # ── Quality breakdown table ──
    q = cs["quality"]
    total_classified = sum(q.values()) or 1

    quality_table = Table(
        title="Client Quality Breakdown",
        box=box.SIMPLE_HEAVY,
        title_style="bold white",
        border_style="bright_black",
        min_width=44,
    )
    quality_table.add_column("Segment", style="bold", min_width=14)
    quality_table.add_column("Count", justify="right", min_width=7)
    quality_table.add_column("Share", justify="right", min_width=7)
    quality_table.add_column("Definition", style="bright_black", min_width=32)

    segments = [
        ("champion", "bold green", "Verified, $10k+ spent, 5+ hires"),
        ("active", "green", "Verified, at least 1 hire"),
        ("new", "yellow", "Verified, never hired"),
        ("risky", "red", "Not payment-verified"),
    ]
    for key, color, definition in segments:
        count = q.get(key, 0)
        pct = round(count / total_classified * 100)
        quality_table.add_row(
            f"[{color}]{key.capitalize()}[/{color}]",
            f"[{color}]{count:,}[/{color}]",
            f"[{color}]{pct}%[/{color}]",
            definition,
        )

    # ── Country table ──
    country_table = Table(
        title="Top Client Countries",
        box=box.SIMPLE_HEAVY,
        title_style="bold white",
        border_style="bright_black",
        min_width=60,
    )
    country_table.add_column("Country", style="bold white", min_width=20)
    country_table.add_column("Jobs", justify="right", style="cyan", min_width=6)
    country_table.add_column("Verified", justify="right", min_width=9)
    country_table.add_column("Hire rate", justify="right", min_width=10)
    country_table.add_column("Avg Hires", justify="right", min_width=9)
    country_table.add_column("Avg Budget", justify="right", style="green", min_width=10)

    for c in cs["countries"][:12]:
        verified_color = (
            "green" if c["verified_pct"] >= 70 else "yellow" if c["verified_pct"] >= 40 else "red"
        )
        budget_str = f"${c['avg_budget']:,.0f}" if c["avg_budget"] > 0 else "—"
        hires_str = f"{c['avg_hires']:.1f}" if c["avg_hires"] > 0 else "—"
        country_table.add_row(
            c.get("country_name") or c["country"],
            f"{c['count']:,}",
            f"[{verified_color}]{c['verified_pct']}%[/{verified_color}]",
            _hire_rate_str(c.get("med_hire_rate")),
            hires_str,
            budget_str,
        )

    console.print(Columns([quality_table, country_table], padding=(0, 4)))

    verified_pct = cs["verified_pct"]
    vcolor = "green" if verified_pct >= 60 else "yellow" if verified_pct >= 30 else "red"
    hr_note = (
        f"  [bright_black]·  median hire rate[/bright_black] {_hire_rate_str(cs['med_hire_rate'])}"
        f" [bright_black](n={cs['hire_rate_n']:,})[/bright_black]"
        if cs.get("med_hire_rate") is not None
        else "  [bright_black]·  hire rate: no posted-jobs counts yet (run `make sync`)[/bright_black]"
    )
    console.print(
        f"  [bright_black]Payment verified clients:[/bright_black] "
        f"[{vcolor}]{verified_pct}%[/{vcolor}]  "
        f"[bright_black]of {cs['total_jobs']:,} jobs in last {days} days[/bright_black]{hr_note}"
    )
    console.print()


def _hire_rate_str(rate: float | None) -> str:
    if rate is None:
        return "[bright_black]—[/bright_black]"
    pct = rate * 100
    color = "green" if pct >= 50 else "yellow" if pct >= 25 else "red"
    return f"[{color}]{pct:.0f}%[/{color}]"


# ─── Volume Heatmap ──────────────────────────────────────────────────────────


def render_volume_heatmap(matrix: list, tz_name: str, days: int, title: str | None = None):
    if not any(v for row in matrix for v in row):
        console.print("[yellow]No volume data. Run:  make sync[/yellow]")
        return

    all_vals = [v for row in matrix for v in row if v > 0]
    max_val = max(all_vals) if all_vals else 1

    console.print()
    heading = title or "JOB POSTING VOLUME  ·  Avg jobs/hr"
    console.rule(f"[bold cyan]{heading}  ·  {tz_name}  ·  Last {days} days[/bold cyan]")
    console.print()

    hr_labels = "00  04  08  12  16  20  "
    console.print(f"[bright_black]       {hr_labels}  Total[/bright_black]")
    console.print(f"[bright_black]      {'─' * 26}[/bright_black]")

    for wd, day in enumerate(DAYS):
        row_vals = matrix[wd]
        day_total = sum(row_vals)
        is_weekend = wd >= 5
        day_style = "bright_black" if is_weekend else "bold white"

        row = Text()
        row.append(f"  {day}  ", style=day_style)
        for hr in range(24):
            val = row_vals[hr]
            ratio = val / max_val
            row.append(_heat_char(ratio), style=_heat_color(ratio))
        row.append(f"  {day_total:>5.0f}", style="bright_black")
        console.print(row)

    console.print()
    _render_legend(max_val)
    console.print()


# ─── Shift Recommendation ────────────────────────────────────────────────────


def render_shift_recommendation(rec: dict, tz_name: str, basis: str = "all postings"):
    start = rec["shift_start"]
    end = rec["shift_end"]
    peak = rec["peak_hour"]
    peak_vol = rec["peak_volume"]

    hourly = rec["hourly_avg"]
    max_h = max(hourly) if hourly else 1
    sparkline = "".join(_spark_char(v / max_h) for v in hourly)

    body = (
        f"\n  [dim]Recommended BD Shift[/dim]   "
        f"[bold green]{start:02d}:00 – {end:02d}:59  ({tz_name})[/bold green]\n"
        f"\n  [dim]Peak Hour[/dim]              "
        f"[bold yellow]{peak:02d}:00[/bold yellow]  [dim]({peak_vol} avg jobs/hr)[/dim]\n"
        f"\n  [dim]Busiest Day[/dim]            [bold green]{rec['best_day']}[/bold green]\n"
        f"  [dim]Slowest Day[/dim]            [red]{rec['worst_day']}[/red]\n"
        f"\n  [dim]24h Pattern[/dim]   "
        f"[bright_black]00h [/bright_black][cyan]{sparkline}[/cyan][bright_black] 23h[/bright_black]\n"
        f"\n  [dim]Best 8-hour window by volume of {basis} (weekdays).[/dim]\n"
    )

    console.print(
        Panel(
            body,
            title="[bold green]BD SHIFT RECOMMENDATION[/bold green]",
            border_style="green",
            expand=False,
        )
    )
    console.print()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _demand_bar(count: int, max_count: int, width: int = 10) -> str:
    ratio = count / max_count if max_count else 0
    filled = round(ratio * width)
    empty = width - filled
    if ratio >= 0.8:
        color = "red"
    elif ratio >= 0.5:
        color = "orange3"
    elif ratio >= 0.3:
        color = "yellow"
    else:
        color = "blue"
    return f"[{color}]{'█' * filled}[/{color}][bright_black]{'░' * empty}[/bright_black]"


def _tier_bar(entry: int, mid: int, expert: int) -> str:
    total = 10
    e_b = max(0, round(entry / 10))
    m_b = max(0, round(mid / 10))
    x_b = max(0, total - e_b - m_b)
    bar = (
        f"[bright_black]{'▪' * e_b}[/bright_black]"
        f"[cyan]{'▪' * m_b}[/cyan]"
        f"[yellow]{'▪' * x_b}[/yellow]"
    )
    return f"{bar}  [bright_black]{entry}%[/bright_black]/[cyan]{mid}%[/cyan]/[yellow]{expert}%[/yellow]"


def _heat_char(ratio: float) -> str:
    if ratio == 0:
        return "·"
    if ratio < 0.15:
        return "░"
    if ratio < 0.35:
        return "▒"
    if ratio < 0.60:
        return "▓"
    return "█"


def _heat_color(ratio: float) -> str:
    if ratio < 0.15:
        return "bright_black"
    if ratio < 0.35:
        return "blue"
    if ratio < 0.60:
        return "yellow"
    if ratio < 0.80:
        return "orange3"
    return "red"


def _spark_char(ratio: float) -> str:
    return "▁▂▃▄▅▆▇█"[min(int(ratio * 8), 7)]


def _render_legend(max_val: float):
    console.print(
        "  Legend: "
        "[bright_black]░ very low[/bright_black]  "
        "[blue]▒ low[/blue]  "
        "[yellow]▓ medium[/yellow]  "
        "[orange3]█ high[/orange3]  "
        "[red]█ peak[/red]"
        f"  [bright_black](peak = {max_val:.0f} jobs/hr)[/bright_black]"
    )
