"""Console rendering for the n8n report."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.models import Job, N8nReport
from core.rich_helpers import (
    bar,
    budget_label,
    color_cell,
    data_as_of_line,
    fmt_band,
    make_table,
    short,
)
from core.scoring import get_scoring
from taxonomies.n8n import abbr_workflow

console = Console()


def render(report: N8nReport, days: int, *, last_fetch: str | None = None) -> None:
    if report.total_jobs == 0:
        console.print(
            Panel(
                "[yellow]No n8n jobs found in this window.[/yellow]\n"
                f"{data_as_of_line(last_fetch)}\n"
                "Try:  [cyan]make sync[/cyan] then [cyan]make n8n N8N_DAYS=14[/cyan]",
                title="n8n Demand",
                border_style="yellow",
            )
        )
        return

    _render_headline(report, days, last_fetch)
    _render_workflow_demand(report)
    _render_industry_demand(report)
    _render_stack_demand(report)
    _render_matrix(report)
    _render_top_opportunities(report)


def _render_headline(report: N8nReport, days: int, last_fetch: str | None) -> None:
    top_wf = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in report.workflow_count[:3]) or "—"
    top_st = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in report.stack_count[:5]) or "—"
    purged = sum(1 for j in report.jobs if j.is_purged)

    body = (
        f"[bold cyan]{report.total_jobs}[/bold cyan] n8n jobs in the last "
        f"[bold]{days}[/bold] days  ·  {data_as_of_line(last_fetch)}\n\n"
        f"[bright_black]Top workflows:[/bright_black] {top_wf}\n"
        f"[bright_black]Top stacks:   [/bright_black] {top_st}\n\n"
        f"[bright_black]Unclassified industry: {report.unclassified_industry}  ·  "
        f"unclassified workflow: {report.unclassified_workflow}  ·  "
        f"analysed from cached labels (text purged): {purged}  ·  "
        f"scoring {get_scoring().version} (`main.py explain <id>`)[/bright_black]"
    )
    console.print(Panel(body, title="n8n Demand · Snapshot", border_style="cyan", padding=(1, 2)))


def _render_workflow_demand(report: N8nReport) -> None:
    items = report.workflow_count
    if not items:
        return

    table = make_table("Workflow Demand  ·  money & competition signals  ·  median (p25–p75)")
    table.add_column("Workflow", style="bold white", min_width=24, no_wrap=True)
    table.add_column("Jobs", justify="right", style="cyan", width=5)
    table.add_column("%", justify="right", style="green", width=4)
    table.add_column("Med $/hr", justify="right", style="green", min_width=14)
    table.add_column("Med Fixed", justify="right", style="yellow", min_width=16)
    table.add_column("Props", justify="right", width=6)
    table.add_column("Verif", justify="right", style="magenta", width=6)

    total = report.total_jobs
    for name, count in items:
        s = report.workflow_stats[name]
        table.add_row(
            name,
            str(count),
            f"{round(count / total * 100)}",
            fmt_band(s.hourly_p25, s.med_hourly, s.hourly_p75, "/hr"),
            fmt_band(s.fixed_p25, s.med_fixed, s.fixed_p75),
            f"{s.med_proposals:.0f}" if s.med_proposals else "—",
            f"{s.verified_pct}%",
        )
    console.print(table)


def _render_industry_demand(report: N8nReport) -> None:
    items = report.industry_count
    if not items:
        console.print(
            Panel(
                "[yellow]No industries detected.[/yellow] "
                "Most n8n posts don't mention a vertical explicitly.",
                border_style="yellow",
            )
        )
        return
    total = report.total_jobs

    table = make_table("Industry Demand  ·  classified jobs only")
    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Industry", style="bold white", ratio=1)
    table.add_column("Jobs", justify="right", style="cyan", min_width=5)
    table.add_column("Share", justify="right", style="green", min_width=6)
    table.add_column("Bar", min_width=20)

    max_count = items[0][1] or 1
    for i, (name, count) in enumerate(items, 1):
        share = round(count / total * 100, 1)
        table.add_row(
            str(i),
            name,
            str(count),
            f"{share}%",
            f"[cyan]{bar(count, max_count, width=20)}[/cyan]",
        )
    console.print(table)


def _render_stack_demand(report: N8nReport) -> None:
    items = report.stack_count
    if not items:
        return
    total = report.total_jobs

    table = make_table("Stack / Tools Frequency  ·  pre-build for these integrations")
    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Tool / Stack", style="bold white", ratio=1)
    table.add_column("Jobs", justify="right", style="cyan", min_width=5)
    table.add_column("% of n8n posts", justify="right", style="green", min_width=14)
    table.add_column("Bar", min_width=18)

    max_count = items[0][1] or 1
    for i, (name, count) in enumerate(items[:20], 1):
        share = round(count / total * 100, 1)
        table.add_row(
            str(i),
            name,
            str(count),
            f"{share}%",
            f"[magenta]{bar(count, max_count, width=18)}[/magenta]",
        )
    console.print(table)


def _render_matrix(report: N8nReport) -> None:
    matrix = report.matrix
    if not matrix:
        return
    industries = [i for i, _ in report.industry_count]
    workflows = [w for w, _ in report.workflow_count]

    industries = [i for i in industries if any(matrix.get(i, {}).values())][:8]
    workflows = [w for w in workflows if any(matrix.get(i, {}).get(w, 0) for i in industries)][:6]
    if not industries or not workflows:
        return

    table = Table(
        title="Industry × Workflow  ·  where each vertical is investing",
        box=box.SIMPLE_HEAVY,
        title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black",
        show_lines=False,
        pad_edge=False,
    )
    table.add_column("Industry", style="bold white", min_width=22, no_wrap=True)
    for wf in workflows:
        table.add_column(abbr_workflow(wf), justify="center", width=8, no_wrap=True)

    for ind in industries:
        row = [ind]
        for wf in workflows:
            row.append(color_cell(matrix.get(ind, {}).get(wf, 0)))
        table.add_row(*row)
    console.print(table)

    legend = "  ".join(
        f"[bright_black]{abbr_workflow(w)}[/bright_black]=[white]{w}[/white]" for w in workflows
    )
    console.print(f"  {legend}\n")


def _render_top_opportunities(report: N8nReport) -> None:
    jobs = report.jobs[:12]
    if not jobs:
        return
    table = Table(
        title=f"Top Opportunities  ·  personal score (scoring {get_scoring().version})",
        caption="Full list w/ industry · stacks · country in: exports/n8n_demand_latest.xlsx",
        caption_style="bright_black",
        box=box.ROUNDED,
        title_style="bold yellow",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column("#", justify="right", style="bold yellow", width=3)
    table.add_column("Title", style="white", min_width=36, no_wrap=True)
    table.add_column("Workflow", style="cyan", width=14, no_wrap=True)
    table.add_column("Budget", justify="right", style="green", width=11, no_wrap=True)
    table.add_column("Props", justify="right", width=6)

    for i, j in enumerate(jobs, 1):
        wf = ", ".join(j.workflows[:1]) or "—"
        verified = " [green]✓[/green]" if j.client_verified else ""
        table.add_row(
            str(i),
            short(j.display_title, 36),
            short(wf, 14),
            _budget_label_short(j),
            str(j.total_applicants) + verified,
        )
    console.print(table)


def _budget_label_short(j: Job) -> str:
    return budget_label(j, short=True)
