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
