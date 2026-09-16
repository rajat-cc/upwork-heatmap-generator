"""Console rendering for the automation report (n8n tables + platform views)."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.rich_helpers import bar, color_cell, data_as_of_line, make_table
from core.scoring import get_scoring
from features.automation.analyzer import AutomationReport
from features.n8n import renderer as n8n
from taxonomies.n8n import abbr_workflow

console = Console()


def render(report: AutomationReport, days: int, *, last_fetch: str | None = None) -> None:
    scope = ", ".join(report.platforms) if report.platforms else "all platforms"
    if report.total_jobs == 0:
        console.print(
            Panel(
                f"[yellow]No automation jobs ({scope}) in the last {days} days.[/yellow]\n"
                f"{data_as_of_line(last_fetch)}\nTry: [cyan]make sync[/cyan]",
                title="Automation demand",
                border_style="yellow",
            )
        )
        return

    base = report.base
    purged = sum(1 for j in base.jobs if j.is_purged)
    top_pl = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in report.platform_count[:4]) or "—"
    top_wf = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in base.workflow_count[:3]) or "—"
    body = (
        f"[bold cyan]{base.total_jobs}[/bold cyan] automation jobs ({scope}) in the last "
        f"[bold]{days}[/bold] days  ·  {data_as_of_line(last_fetch)}\n\n"
        f"[bright_black]Top platforms:[/bright_black] {top_pl}\n"
        f"[bright_black]Top workflows:[/bright_black] {top_wf}\n\n"
        f"[bright_black]Unclassified industry: {base.unclassified_industry}  ·  "
        f"unclassified workflow: {base.unclassified_workflow}  ·  "
        f"from cached labels (text purged): {purged}  ·  scoring {get_scoring().version}[/bright_black]"
    )
    console.print(
        Panel(body, title="Automation Demand · Snapshot", border_style="cyan", padding=(1, 2))
    )

    _render_platforms(report)
    n8n._render_workflow_demand(base)
    n8n._render_industry_demand(base)
    n8n._render_stack_demand(base)
    _render_platform_matrix(report)
    n8n._render_matrix(base)
    n8n._render_top_opportunities(base)


def _render_platforms(report: AutomationReport) -> None:
    items = report.platform_count
    if not items:
        return
    table = make_table("Platform demand  ·  which automation tools clients name")
    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Platform", style="bold white", ratio=1)
    table.add_column("Jobs", justify="right", style="cyan", min_width=5)
    table.add_column("Share", justify="right", style="green", min_width=6)
    table.add_column("Bar", min_width=20)
    total = report.total_jobs or 1
    top = items[0][1] or 1
    for i, (name, count) in enumerate(items, 1):
        table.add_row(
            str(i),
            name,
            str(count),
            f"{round(count / total * 100, 1)}%",
            f"[cyan]{bar(count, top)}[/cyan]",
        )
    console.print(table)


def _render_platform_matrix(report: AutomationReport) -> None:
    matrix = report.platform_matrix
    if not matrix:
        return
    platforms = [p for p, _ in report.platform_count if any(matrix.get(p, {}).values())][:8]
    workflows = [
        w
        for w, _ in report.base.workflow_count
        if any(matrix.get(p, {}).get(w, 0) for p in platforms)
    ][:6]
    if not platforms or not workflows:
        return
    table = Table(
        title="Platform × Workflow  ·  what each tool gets used for",
        box=box.SIMPLE_HEAVY, title_style="bold cyan", header_style="bold white on grey23",
        border_style="bright_black", show_lines=False, pad_edge=False,
    )  # fmt: skip
    table.add_column("Platform", style="bold white", min_width=20, no_wrap=True)
    for wf in workflows:
        table.add_column(abbr_workflow(wf), justify="center", width=8, no_wrap=True)
    for p in platforms:
        table.add_row(p, *[color_cell(matrix.get(p, {}).get(wf, 0)) for wf in workflows])
    console.print(table)
