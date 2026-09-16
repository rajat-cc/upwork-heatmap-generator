"""Rich rendering primitives shared across feature renderers."""

from __future__ import annotations

from rich import box
from rich.table import Table


def make_table(title: str, *, expand: bool = False) -> Table:
    """Standard rounded table with our colour palette."""
    return Table(
        title=title,
        box=box.ROUNDED,
        title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black",
        expand=expand,
    )


def short(s: str | None, n: int) -> str:
    """Trim s to n chars, replacing newlines with spaces and adding `…` overflow."""
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt_money(val: float, suffix: str = "") -> str:
    """`$1.2k` / `$45` style; returns `—` for zero/None."""
    if not val:
        return "—"
    if val >= 1000:
        return f"${val / 1000:.1f}k{suffix}"
    return f"${val:.0f}{suffix}"


def color_cell(val: int) -> str:
    """Heatmap cell colouring used in matrix-style tables."""
    if val == 0:
        return "[bright_black]·[/bright_black]"
    if val >= 10:
        return f"[bold red]{val}[/bold red]"
    if val >= 5:
        return f"[bold yellow]{val}[/bold yellow]"
    return f"[green]{val}[/green]"


def bar(value: int, max_value: int, *, width: int = 20, ch: str = "█") -> str:
    """Unicode bar `█████░░░` proportional to value/max_value."""
    if max_value <= 0:
        return ""
    filled = max(1, round(value / max_value * width)) if value > 0 else 0
    return ch * filled


def budget_label(job, *, short: bool = False) -> str:
    """`$80-150/hr` / `$5,000 fixed`; short form `$5.0k fxd` for tight columns."""
    if job.budget_type == "HOURLY":
        if job.budget_min and job.budget_max:
            return f"${job.budget_min:.0f}-{job.budget_max:.0f}/hr"
        if job.budget_amount:
            return f"${job.budget_amount:.0f}/hr"
        return "hourly" if short else "Hourly"
    if job.budget_type == "FIXED" and job.budget_amount:
        amt = job.budget_amount
        if short:
            return f"${amt / 1000:.1f}k fxd" if amt >= 1000 else f"${amt:.0f} fxd"
        return f"${amt:,.0f} fixed"
    return "—"


def fmt_band(p25: float, p50: float, p75: float, suffix: str = "") -> str:
    """`$25/hr (18–40)`: the median with its p25–p75 band; `—` when empty."""
    if not p50:
        return "—"
    mid = fmt_money(p50, suffix)
    if p25 and p75 and (p25 != p50 or p75 != p50):
        return f"{mid} [bright_black]({p25:.0f}–{p75:.0f})[/bright_black]"
    return mid


def hours_ago(iso: str | None) -> float | None:
    from db import _now_iso, hours_between  # local import: db imports config, not this module

    return hours_between(iso, _now_iso()) if iso else None


def data_as_of_line(last_fetch_iso: str | None, *, stale_after_hours: int | None = None) -> str:
    """One line every screen starts with: when the data was last refreshed."""
    from config import STALE_AFTER_HOURS

    limit = STALE_AFTER_HOURS if stale_after_hours is None else stale_after_hours
    if not last_fetch_iso:
        return "[red]data as of: no successful fetch yet  ·  run `make sync` or `make auth`[/red]"
    age = hours_ago(last_fetch_iso)
    stamp = last_fetch_iso.replace("T", " ")[:16] + "Z"
    if age is None:
        return f"[yellow]data as of {stamp}[/yellow]"
    colour = "red" if age > limit else "green"
    label = f"{age:.1f} h ago" if age < 48 else f"{age / 24:.0f} days ago"
    note = "  ·  STALE" if age > limit else ""
    return f"[{colour}]data as of {stamp} ({label}){note}[/{colour}]"


def provenance_footer(
    *, days: int, n: int, runs: int, last_fetch_iso: str | None, extra: str = ""
) -> str:
    """Dim footer printed under every table: window, sample, runs, freshness."""
    fetched = (last_fetch_iso or "never").replace("T", " ")[:16]
    bits = [
        f"window {days}d",
        f"sample {n:,} jobs",
        f"{runs} fetch run{'s' if runs != 1 else ''} in window",
        f"last fetch {fetched}",
    ]
    if extra:
        bits.append(extra)
    return "  [bright_black]" + "  ·  ".join(bits) + "[/bright_black]"
