"""Clients lens: render and export repeat-poster accounts."""

from __future__ import annotations

import os
from datetime import datetime

from openpyxl import Workbook
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

import config
from core.lens import run_lens
from core.rich_helpers import data_as_of_line, fmt_money, make_table, provenance_footer
from core.xlsx_helpers import LEFT, RIGHT, write_header
from db import count_fetch_runs, get_last_success
from features.clients import analyzer
from features.clients.analyzer import ClientsReport

console = Console()


class ClientsLens:
    name = "clients"

    def analyze(self, days: int) -> ClientsReport:
        return analyzer.analyze(days)

    def render(self, report: ClientsReport, days: int) -> None:
        last = get_last_success()
        console.print(Rule(f"[bold cyan]Client Accounts · Last {days} days[/bold cyan]"))
        console.print(f"  {data_as_of_line(last)}")
        repeat = report.repeat
        if not repeat:
            console.print(
                Panel(
                    f"[yellow]No client posted twice in the last {days} days (heuristic match).[/yellow]",
                    border_style="yellow",
                )
            )
        else:
            table = make_table(
                "Likely repeat posters  ·  exact = public company id from a detail fetch, "
                "heuristic = same country, spend, hires and posted count"
            )
            table.add_column("Client", style="bold white", min_width=16)
            table.add_column("Match", min_width=9)
            table.add_column("Segment", min_width=9)
            table.add_column("Posts", justify="right", style="cyan", min_width=5)
            table.add_column("Spent", justify="right", style="green", min_width=8)
            table.add_column("Hires", justify="right", min_width=5)
            table.add_column("Hire rate", justify="right", min_width=9)
            table.add_column("Workflows", min_width=22)
            table.add_column("Platforms", min_width=12)
            table.add_column("You", justify="right", min_width=8)
            table.add_column("Last post", min_width=10)
            for r in repeat[:25]:
                rate = f"{r.hire_rate * 100:.0f}%" if r.hire_rate is not None else "—"
                you = f"{r.your_hired}/{r.your_submitted}" if r.your_submitted else "—"
                table.add_row(
                    f"{r.country_name} · {r.label}", r.match, r.segment, str(r.posts), fmt_money(r.spent),
                    str(r.hires), rate, ", ".join(r.workflows) or "—", ", ".join(r.platforms) or "—",
                    you, r.last_seen[:10],
                )  # fmt: skip
            console.print(table)
        console.print(
            provenance_footer(
                days=days,
                n=report.jobs,
                runs=count_fetch_runs(since_iso=report.since),
                last_fetch_iso=last,
                extra=f"{len(report.rows)} fingerprinted clients · {len(repeat)} repeat",
            )
        )

    def export(self, report: ClientsReport, days: int) -> str | None:
        if not report.rows:
            return None
        os.makedirs(config.EXPORTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        path = os.path.join(config.EXPORTS_DIR, f"clients_{ts}.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "Clients"
        write_header(
            ws,
            ["Key", "Match", "Country", "Segment", "Posts", "Spent", "Hires", "Posted", "Hire rate %",
             "Workflows", "Platforms", "Your submitted", "Your hired", "First post", "Last post", "Job ids"],
            [14, 10, 18, 10, 6, 10, 6, 7, 11, 30, 20, 13, 10, 12, 12, 40],
        )  # fmt: skip
        for i, r in enumerate(report.rows, 2):
            values = [
                r.key, r.match, r.country_name, r.segment, r.posts, r.spent, r.hires, r.posted,
                round(r.hire_rate * 100) if r.hire_rate is not None else None,
                ", ".join(r.workflows), ", ".join(r.platforms), r.your_submitted, r.your_hired,
                r.first_seen[:10], r.last_seen[:10], ", ".join(r.job_ids),
            ]  # fmt: skip
            for c, v in enumerate(values, 1):
                ws.cell(row=i, column=c, value=v).alignment = (
                    RIGHT if c in (5, 6, 7, 8, 9, 12, 13) else LEFT
                )
        ws.freeze_panes = "A2"
        wb.save(path)
        wb.save(os.path.join(config.EXPORTS_DIR, "clients_latest.xlsx"))
        return path


def run(days: int = 90, *, export: bool = True) -> ClientsReport:
    return run_lens(ClientsLens(), days, export=export)
