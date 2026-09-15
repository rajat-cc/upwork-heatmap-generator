import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

EXPORTS_DIR = "exports"
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ── Palette ──────────────────────────────────────────────────────────────────
_HDR_FILL = PatternFill("solid", fgColor="1F3864")  # dark navy
_HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
_ALT_FILL = PatternFill("solid", fgColor="EEF2F7")  # light blue-grey
_BOLD = Font(bold=True, size=10)
_NORMAL = Font(size=10)
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center")
_RIGHT = Alignment(horizontal="right", vertical="center")
_THIN = Side(style="thin", color="CCCCCC")
_BORDER = Border(bottom=_THIN)

_GREEN_FILL = PatternFill("solid", fgColor="D6EAD6")
_RED_FILL = PatternFill("solid", fgColor="F4CCCC")
_YLW_FILL = PatternFill("solid", fgColor="FFF2CC")


# ── Public entry point ────────────────────────────────────────────────────────


def export_dashboard(
    skills_data: list,
    client_data: dict,
    volume_matrix: list,
    shift_rec: dict,
    tz_name: str,
    days: int,
) -> str:
    """Build a timestamped Excel workbook and return its path."""
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = os.path.join(EXPORTS_DIR, f"upwork_dashboard_{ts}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)  # remove default blank sheet

    _sheet_skills(wb, skills_data, days)
    _sheet_client_quality(wb, client_data, days)
    _sheet_client_countries(wb, client_data)
    _sheet_volume_heatmap(wb, volume_matrix, tz_name, days)
    _sheet_shift_summary(wb, shift_rec, tz_name)

    wb.save(path)
    # Keep a fixed "latest" copy for easy access
    latest = os.path.join(EXPORTS_DIR, "latest.xlsx")
    wb.save(latest)
    return path


# ── Sheet builders ────────────────────────────────────────────────────────────


def _sheet_skills(wb: Workbook, data: list, days: int):
    ws = wb.create_sheet("Skills Demand")
    ws.freeze_panes = "A2"

    headers = [
        "#",
        "Skill",
        "Jobs",
        "Trend %",
        "Opp Score",
        "Avg Proposals",
        "Avg Hourly ($)",
        "Avg Fixed ($)",
        "% Hourly",
        "Entry %",
        "Mid %",
        "Expert %",
    ]
    col_widths = [4, 22, 8, 10, 10, 14, 14, 14, 10, 9, 9, 9]
    col_aligns = [
        _CENTER,
        _LEFT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
        _RIGHT,
    ]

    _write_header(ws, headers, col_widths)

    for i, row in enumerate(data, 1):
        r = i + 1
        fill = _ALT_FILL if i % 2 == 0 else None
        trend = row["trend_pct"]
        trend_str = "new" if trend is None else f"{trend:+.0f}%"
        values = [
            i,
            row["skill"],
            row["count"],
            trend_str,
            row["opportunity_score"],
            round(row["avg_proposals"], 1) if row["avg_proposals"] else 0,
            round(row["avg_hourly"], 2) if row["avg_hourly"] else 0,
            round(row["avg_fixed"], 2) if row["avg_fixed"] else 0,
            row["hourly_pct"],
            row["entry_pct"],
            row["mid_pct"],
            row["exp_pct"],
        ]
        for c, (val, align) in enumerate(zip(values, col_aligns, strict=False), 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.alignment = align
            cell.font = _NORMAL
            if fill:
                cell.fill = fill

    # Conditional formatting — Opp Score (col E = 5): green-yellow-red scale
    last = len(data) + 1
    ws.conditional_formatting.add(
        f"E2:E{last}",
        ColorScaleRule(
            start_type="num",
            start_value=0,
            start_color="F4CCCC",
            mid_type="num",
            mid_value=45,
            mid_color="FFF2CC",
            end_type="num",
            end_value=100,
            end_color="D6EAD6",
        ),
    )
    # Data bar for Jobs (col C = 3)
    ws.conditional_formatting.add(
        f"C2:C{last}",
        DataBarRule(start_type="min", end_type="max", color="4472C4", showValue=True),
    )

    ws.sheet_properties.tabColor = "1F3864"
    ws["A1"].value = f"Skills Demand — Last {days} days"
    ws.insert_rows(1)
    _style_title_row(ws, 1, len(headers), f"Skills Demand — Last {days} days")


def _sheet_client_quality(wb: Workbook, cs: dict, days: int):
    ws = wb.create_sheet("Client Quality")
    ws.freeze_panes = "A2"

    headers = ["Segment", "Count", "Share %", "Definition"]
    col_widths = [14, 10, 10, 42]
    _write_header(ws, headers, col_widths)

    q = cs.get("quality", {})
    total = sum(q.values()) or 1
    segments = [
        ("Champion", q.get("champion", 0), "Verified, $10k+ spent, 5+ hires"),
        ("Active", q.get("active", 0), "Verified, ≥1 hire"),
        ("New", q.get("new", 0), "Verified, never hired"),
        ("Risky", q.get("risky", 0), "Unverified or 0 hires"),
    ]
    seg_fills = [
        PatternFill("solid", fgColor="D6EAD6"),  # champion — green
        PatternFill("solid", fgColor="EAF4D3"),  # active   — light green
        PatternFill("solid", fgColor="FFF2CC"),  # new      — yellow
        PatternFill("solid", fgColor="F4CCCC"),  # risky    — red
    ]
    for i, ((label, count, defn), fill) in enumerate(zip(segments, seg_fills, strict=False), 2):
        pct = round(count / total * 100)
        row_vals = [label, count, pct, defn]
        row_aligns = [_LEFT, _RIGHT, _RIGHT, _LEFT]
        for c, (val, align) in enumerate(zip(row_vals, row_aligns, strict=False), 1):
            cell = ws.cell(row=i, column=c, value=val)
            cell.fill = fill
            cell.alignment = align
            cell.font = _BOLD if c == 1 else _NORMAL

    # Summary row
    r = len(segments) + 3
    ws.cell(row=r, column=1, value="Payment Verified %").font = _BOLD
    ws.cell(row=r, column=2, value=cs.get("verified_pct", 0)).alignment = _RIGHT
    ws.cell(row=r, column=3, value="of all jobs").font = Font(italic=True, size=10)

    _style_title_row(ws, 1, len(headers), f"Client Quality Breakdown — Last {days} days")


def _sheet_client_countries(wb: Workbook, cs: dict):
    ws = wb.create_sheet("Client Countries")
    ws.freeze_panes = "A2"

    headers = ["Country", "Jobs", "Verified %", "Avg Hires", "Avg Budget ($)"]
    col_widths = [24, 8, 12, 12, 16]
    col_aligns = [_LEFT, _RIGHT, _RIGHT, _RIGHT, _RIGHT]
    _write_header(ws, headers, col_widths)

    for i, c_data in enumerate(cs.get("countries", []), 1):
        r = i + 1
        fill = _ALT_FILL if i % 2 == 0 else None
        values = [
            c_data["country"],
            c_data["count"],
            c_data["verified_pct"],
            round(c_data["avg_hires"], 1) if c_data["avg_hires"] else 0,
            round(c_data["avg_budget"], 2) if c_data["avg_budget"] else 0,
        ]
        for col, (val, align) in enumerate(zip(values, col_aligns, strict=False), 1):
            cell = ws.cell(row=r, column=col, value=val)
            cell.alignment = align
            cell.font = _NORMAL
            if fill:
                cell.fill = fill

    # Color-scale Verified % (col C)
    last = len(cs.get("countries", [])) + 1
    ws.conditional_formatting.add(
        f"C2:C{last}",
        ColorScaleRule(
            start_type="num",
            start_value=0,
            start_color="F4CCCC",
            mid_type="num",
            mid_value=50,
            mid_color="FFF2CC",
            end_type="num",
            end_value=100,
            end_color="D6EAD6",
        ),
    )
    _style_title_row(ws, 1, len(headers), "Top Client Countries")


def _sheet_volume_heatmap(wb: Workbook, matrix: list, tz_name: str, days: int):
    ws = wb.create_sheet("Volume Heatmap")
    ws.freeze_panes = "B2"

    # Column headers: Day | 00h | 01h | … | 23h | Total
    ws.cell(row=1, column=1, value="Day")
    _hdr_style(ws.cell(row=1, column=1))
    ws.column_dimensions["A"].width = 7

    for hr in range(24):
        cell = ws.cell(row=1, column=hr + 2, value=f"{hr:02d}h")
        _hdr_style(cell)
        ws.column_dimensions[get_column_letter(hr + 2)].width = 5

    total_col = 26
    cell = ws.cell(row=1, column=total_col, value="Total")
    _hdr_style(cell)
    ws.column_dimensions[get_column_letter(total_col)].width = 8

    for wd, day in enumerate(DAYS_OF_WEEK):
        r = wd + 2
        cell = ws.cell(row=r, column=1, value=day)
        cell.font = Font(bold=(wd < 5), size=10)
        cell.alignment = _CENTER

        row_total = 0
        for hr in range(24):
            val = matrix[wd][hr]
            row_total += val
            c = ws.cell(row=r, column=hr + 2, value=round(val, 1))
            c.alignment = _CENTER
            c.font = _NORMAL

        ws.cell(row=r, column=total_col, value=round(row_total, 1)).alignment = _RIGHT

    # Color scale over the data range B2:Y8 (24 hours × 7 days)
    ws.conditional_formatting.add(
        "B2:Y8",
        ColorScaleRule(
            start_type="min",
            start_color="FFFFFF",
            mid_type="percentile",
            mid_value=50,
            mid_color="FFD966",
            end_type="max",
            end_color="C00000",
        ),
    )
    _style_title_row(
        ws,
        1,
        total_col,
        f"Job Posting Volume (avg jobs/hr)  ·  {tz_name}  ·  Last {days} days",
        insert=False,
    )


def _sheet_shift_summary(wb: Workbook, rec: dict, tz_name: str):
    ws = wb.create_sheet("BD Shift Summary")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 30

    rows = [
        (
            "Recommended BD Shift",
            f"{rec['shift_start']:02d}:00 – {rec['shift_end']:02d}:59  ({tz_name})",
        ),
        ("Peak Hour", f"{rec['peak_hour']:02d}:00  ({rec['peak_volume']} avg jobs/hr)"),
        ("Busiest Day", rec["best_day"]),
        ("Slowest Day", rec["worst_day"]),
    ]

    fills = [
        PatternFill("solid", fgColor="D6EAD6"),  # shift   — green
        PatternFill("solid", fgColor="FFF2CC"),  # peak    — yellow
        PatternFill("solid", fgColor="EAF4D3"),  # busiest — light green
        PatternFill("solid", fgColor="F4CCCC"),  # slowest — red
    ]

    for i, ((label, value), fill) in enumerate(zip(rows, fills, strict=False), 2):
        lc = ws.cell(row=i, column=1, value=label)
        vc = ws.cell(row=i, column=2, value=value)
        for cell in (lc, vc):
            cell.fill = fill
            cell.alignment = _LEFT
        lc.font = _BOLD
        vc.font = _NORMAL

    # 24h sparkline as raw numbers in a row below
    ws.cell(row=8, column=1, value="24h Avg Volume (jobs/hr)").font = _BOLD
    for hr, val in enumerate(rec["hourly_avg"]):
        c = ws.cell(row=9, column=hr + 1, value=round(val, 1))
        c.alignment = _CENTER
        c.font = Font(size=9)

    ws.conditional_formatting.add(
        f"A9:{get_column_letter(24)}9",
        ColorScaleRule(
            start_type="min",
            start_color="FFFFFF",
            end_type="max",
            end_color="4472C4",
        ),
    )

    _style_title_row(ws, 1, 4, "BD Shift Recommendation", insert=False)
    ws.cell(row=1, column=1).value = "BD Shift Recommendation"


# ── Styling helpers ───────────────────────────────────────────────────────────


def _hdr_style(cell):
    cell.fill = _HDR_FILL
    cell.font = _HDR_FONT
    cell.alignment = _CENTER


def _write_header(ws, headers: list, col_widths: list):
    for c, (h, w) in enumerate(zip(headers, col_widths, strict=False), 1):
        cell = ws.cell(row=1, column=c, value=h)
        _hdr_style(cell)
        ws.column_dimensions[get_column_letter(c)].width = w


def _style_title_row(ws, row: int, ncols: int, title: str, insert: bool = True):
    if insert:
        ws.insert_rows(row)
    cell = ws.cell(row=row, column=1, value=title)
    cell.font = Font(bold=True, size=12, color="1F3864")
    cell.alignment = _LEFT
    ws.row_dimensions[row].height = 20
