"""n8n automation demand analysis.

Pulls jobs from the last N days where 'n8n' appears in the skills, title,
or description, then segments them by:
  - Workflow type (what the automation does)
  - Industry vertical (who is buying it)
  - Stack/Tools (which integrations recur)

The goal is actionable signal: which automations should I pre-build, for
which industry, on which stack — sorted by demand and opportunity score.
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from db import get_conn, save_classifications, search_jobs_fts

console = Console()
EXPORTS_DIR = "exports"

N8N_RE = re.compile(r"\bn8n\b", re.IGNORECASE)

# ─── Taxonomies ──────────────────────────────────────────────────────────────
# Phrases are matched with word boundaries — see _compile_taxonomy().
# A job can match multiple categories within an axis (multi-tag).

INDUSTRIES = [
    ("Healthcare",            ["healthcare", "medical practice", "clinic", "hospital",
                               "patient intake", "dentist", "telehealth", "telemedicine",
                               "ehr", "emr", "hipaa", "physician", "therapist",
                               "wellness clinic", "mental health"]),
    ("E-commerce",            ["shopify", "woocommerce", "ecommerce", "e-commerce",
                               "online store", "amazon seller", "etsy seller",
                               "product catalog", "dropshipping", "magento",
                               "bigcommerce"]),
    ("Real Estate",           ["real estate", "realtor", "realty", "mls listing",
                               "property management", "airbnb host", "rental property",
                               "zillow", "tenant", "landlord"]),
    ("Finance / Fintech",     ["fintech", "trading bot", "crypto", "accounting firm",
                               "bookkeeping", "payroll", "invoicing automation",
                               "stripe automation", "quickbooks", "xero",
                               "loan officer", "mortgage"]),
    ("Marketing / Agency",    ["marketing agency", "smma", "seo agency", "ad agency",
                               "ppc agency", "ad campaign", "lead generation agency",
                               "growth agency", "performance marketing"]),
    ("Education / EdTech",    ["edtech", "online course", "lms", "tutoring",
                               "student enrollment", "training program", "course platform",
                               "udemy", "teachable"]),
    ("HR / Recruiting",       ["recruiting", "recruitment", "applicant tracking",
                               "ats system", "hiring pipeline", "talent acquisition",
                               "onboarding automation", "people ops"]),
    ("Legal",                 ["law firm", "attorney", "lawyer", "legal practice",
                               "paralegal", "contract review"]),
    ("Travel / Hospitality",  ["hotel booking", "travel agency", "restaurant",
                               "hospitality", "tour operator"]),
    ("Logistics / Supply",    ["logistics", "shipping automation", "supply chain",
                               "warehouse management", "inventory management",
                               "fulfillment", "freight", "3pl"]),
    ("Media / Content",       ["podcast", "youtube channel", "blog", "publishing",
                               "newsletter", "media company", "influencer",
                               "content creator"]),
    ("SaaS / B2B",            ["saas", "b2b software", "developer tools"]),
    ("Construction / Trades", ["construction", "general contractor", "plumbing",
                               "electrician", "hvac", "roofing", "trades business"]),
    ("Coaching / Consulting", ["coach", "coaching business", "consulting firm",
                               "consultancy"]),
    ("Non-Profit",            ["non-profit", "nonprofit", "ngo", "charity"]),
]

WORKFLOWS = [
    ("AI / LLM Agents",        ["openai", "gpt-3", "gpt-4", "gpt-5", "gpt", "llm",
                                "ai agent", "chatgpt", "anthropic", "claude", "rag",
                                "vector database", "pinecone", "weaviate", "embedding",
                                "ai chatbot", "agentic"]),
    ("Voice / Telephony",      ["voice agent", "vapi", "retell", "twilio", "phone call",
                                "ivr", "voice bot", "voice ai", "elevenlabs"]),
    ("Lead Gen / Scraping",    ["lead generation", "leadgen", "web scraping", "scraper",
                                "linkedin scraping", "apollo.io", "prospecting",
                                "lead enrichment", "data scraping", "playwright"]),
    ("Email Automation",       ["email automation", "gmail automation", "mailchimp",
                                "sendgrid", "newsletter", "drip campaign", "cold email",
                                "smartlead", "instantly.ai", "lemlist"]),
    ("CRM Sync",               ["crm", "hubspot", "salesforce", "pipedrive", "zoho crm",
                                "monday.com", "gohighlevel", "ghl", "close.com"]),
    ("Slack / Discord / Chat", ["slack", "discord", "ms teams", "telegram", "whatsapp"]),
    ("Social Media Auto",      ["instagram", "facebook page", "tiktok", "linkedin post",
                                "social media post", "content calendar",
                                "social media automation"]),
    ("Data Sync / ETL",        ["airtable", "google sheet", "google sheets", "notion",
                                "etl", "data pipeline", "data sync", "supabase"]),
    ("Document / PDF / OCR",   ["pdf parsing", "ocr", "invoice processing",
                                "document parsing", "extract data from pdf",
                                "contract parsing"]),
    ("Webhook / API Glue",     ["webhook", "api integration", "rest api",
                                "third-party api", "third party api"]),
    ("Reporting / Analytics",  ["dashboard", "kpi", "weekly report", "daily report",
                                "analytics workflow", "reporting automation",
                                "looker studio", "metabase"]),
    ("Customer Support Bots",  ["chatbot", "support ticket", "zendesk", "intercom",
                                "freshdesk", "helpdesk", "customer support automation"]),
    ("Calendar / Scheduling",  ["calendly", "google calendar", "appointment booking",
                                "scheduling automation", "cal.com"]),
    ("E-com Order Processing", ["order processing", "order fulfillment",
                                "shopify order", "woocommerce order",
                                "shipment tracking"]),
]

# Stacks: specific tools/products. Same job often surfaces multiple stacks.
STACKS = [
    ("OpenAI / GPT",     ["openai", "gpt-3", "gpt-4", "gpt-5", "gpt", "chatgpt"]),
    ("Anthropic Claude", ["anthropic", "claude"]),
    ("Vapi",             ["vapi"]),
    ("Retell",           ["retell"]),
    ("Twilio",           ["twilio"]),
    ("ElevenLabs",       ["elevenlabs"]),
    ("Make / Integromat",["make.com", "integromat"]),
    ("Zapier",           ["zapier"]),
    ("GoHighLevel",      ["gohighlevel", "ghl"]),
    ("HubSpot",          ["hubspot"]),
    ("Salesforce",       ["salesforce"]),
    ("Pipedrive",        ["pipedrive"]),
    ("Airtable",         ["airtable"]),
    ("Supabase",         ["supabase"]),
    ("Notion",           ["notion"]),
    ("Google Sheets",    ["google sheet", "google sheets"]),
    ("Slack",            ["slack"]),
    ("Discord",          ["discord"]),
    ("WhatsApp",         ["whatsapp"]),
    ("Telegram",         ["telegram"]),
    ("Shopify",          ["shopify"]),
    ("WooCommerce",      ["woocommerce"]),
    ("Stripe",           ["stripe"]),
    ("Calendly",         ["calendly"]),
    ("Cal.com",          ["cal.com"]),
    ("Zendesk",          ["zendesk"]),
    ("Intercom",         ["intercom"]),
    ("Mailchimp",        ["mailchimp"]),
    ("SendGrid",         ["sendgrid"]),
    ("Instantly",        ["instantly.ai", "instantly"]),
    ("Smartlead",        ["smartlead"]),
    ("Lemlist",          ["lemlist"]),
    ("Apollo.io",        ["apollo.io"]),
    ("LinkedIn",         ["linkedin"]),
    ("Pinecone",         ["pinecone"]),
    ("Playwright",       ["playwright"]),
    ("Selenium",         ["selenium"]),
    ("PostgreSQL",       ["postgres", "postgresql"]),
    ("MongoDB",          ["mongodb"]),
]


def _compile_taxonomy(taxonomy):
    """Compile each category to a single word-boundary regex (case-insensitive)."""
    compiled = []
    for label, kws in taxonomy:
        pattern = r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b"
        compiled.append((label, re.compile(pattern, re.IGNORECASE)))
    return compiled


INDUSTRIES_RE = _compile_taxonomy(INDUSTRIES)
WORKFLOWS_RE  = _compile_taxonomy(WORKFLOWS)
STACKS_RE     = _compile_taxonomy(STACKS)


# ─── Public API ──────────────────────────────────────────────────────────────

def fetch_n8n_jobs_from_api(days: int = 7, limit: int = 1000) -> int:
    """Pull n8n keyword search from Upwork into the local DB."""
    from fetcher import fetch_jobs

    return fetch_jobs(search_term="n8n", limit=limit, since_days=days)


def get_n8n_jobs(days: int = 7) -> list[dict]:
    """All jobs in the window where 'n8n' is in skills, title, or description.

    Two-stage filter:
      1) FTS5 `MATCH 'n8n'` on title+description+skills — fast index scan,
         narrows the working set to candidate jobs only
      2) Strict `\\bn8n\\b` regex re-validation in Python — eliminates substring
         false positives ("n8nx", URLs containing "n8n", etc.)
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    candidate_ids = search_jobs_fts("n8n", since_iso=cutoff)
    if not candidate_ids:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, title, description, skills, published_at, category,
                   contractor_tier, budget_type, budget_amount, budget_min, budget_max,
                   total_applicants, client_total_hires, client_total_spent,
                   client_verified, client_country, url
            FROM jobs
            WHERE id IN ({placeholders})
            """,
            candidate_ids,
        ).fetchall()

    matches = []
    for r in rows:
        skills = json.loads(r["skills"] or "[]")
        haystack = " ".join([
            r["title"] or "",
            r["description"] or "",
            " ".join(skills),
        ])
        if not N8N_RE.search(haystack):
            continue
        matches.append({
            "id":               r["id"],
            "title":            r["title"] or "",
            "description":      r["description"] or "",
            "skills":           skills,
            "published_at":     r["published_at"] or "",
            "category":         r["category"] or "",
            "contractor_tier":  r["contractor_tier"] or "",
            "budget_type":      r["budget_type"] or "",
            "budget_amount":    float(r["budget_amount"] or 0),
            "budget_min":       float(r["budget_min"] or 0),
            "budget_max":       float(r["budget_max"] or 0),
            "total_applicants": int(r["total_applicants"] or 0),
            "client_country":   r["client_country"] or "",
            "client_verified":  int(r["client_verified"] or 0),
            "client_hires":     int(r["client_total_hires"] or 0),
            "client_spent":     float(r["client_total_spent"] or 0),
            "url":              r["url"] or f"https://www.upwork.com/jobs/{r['id']}",
        })
    return matches


def persist_classifications(jobs: list[dict]) -> None:
    """Write each job's regex classifications into job_classifications cache."""
    rows = []
    for j in jobs:
        for label in j.get("industries", []):
            rows.append({"job_id": j["id"], "axis": "industry", "label": label, "source": "regex"})
        for label in j.get("workflows", []):
            rows.append({"job_id": j["id"], "axis": "workflow", "label": label, "source": "regex"})
        for label in j.get("stacks", []):
            rows.append({"job_id": j["id"], "axis": "stack", "label": label, "source": "regex"})
    save_classifications(rows)


def analyze(jobs: list[dict]) -> dict:
    """Tag each job with industry / workflow / stack and aggregate."""
    industry_count = Counter()
    workflow_count = Counter()
    stack_count    = Counter()
    matrix         = defaultdict(lambda: defaultdict(int))   # industry -> workflow -> N

    workflow_jobs = defaultdict(list)   # for per-workflow money/competition stats
    industry_jobs = defaultdict(list)

    unclassified_industry = 0
    unclassified_workflow = 0

    for job in jobs:
        text = f"{job['title']} {job['description']} {' '.join(job['skills'])}"
        industries = _match(text, INDUSTRIES_RE)
        workflows  = _match(text, WORKFLOWS_RE)
        stacks     = _match(text, STACKS_RE)

        job["industries"] = industries
        job["workflows"]  = workflows
        job["stacks"]     = stacks
        job["opp_score"]  = _opportunity_score(job)

        if not industries:
            unclassified_industry += 1
        if not workflows:
            unclassified_workflow += 1

        for ind in industries:
            industry_count[ind] += 1
            industry_jobs[ind].append(job)
        for wf in workflows:
            workflow_count[wf] += 1
            workflow_jobs[wf].append(job)
        for st in stacks:
            stack_count[st] += 1

        for ind in industries:
            for wf in workflows:
                matrix[ind][wf] += 1

    workflow_stats = {wf: _money_stats(js) for wf, js in workflow_jobs.items()}

    return {
        "total_jobs":            len(jobs),
        "industry_count":        industry_count.most_common(),
        "workflow_count":        workflow_count.most_common(),
        "stack_count":           stack_count.most_common(),
        "matrix":                matrix,
        "workflow_stats":        workflow_stats,
        "industry_jobs":         industry_jobs,
        "workflow_jobs":         workflow_jobs,
        "unclassified_industry": unclassified_industry,
        "unclassified_workflow": unclassified_workflow,
        "jobs":                  sorted(jobs, key=lambda j: j["opp_score"], reverse=True),
    }


# ─── Console rendering ───────────────────────────────────────────────────────

def render(report: dict, days: int):
    total = report["total_jobs"]
    if total == 0:
        console.print(Panel(
            "[yellow]No n8n jobs found in this window.[/yellow]\n"
            "Try:  [cyan]make n8n N8N_DAYS=14[/cyan]",
            title="n8n Demand", border_style="yellow",
        ))
        return

    _render_headline(report, days)
    _render_workflow_demand(report)
    _render_industry_demand(report)
    _render_stack_demand(report)
    _render_matrix(report)
    _render_top_opportunities(report)


def _render_headline(report: dict, days: int):
    total = report["total_jobs"]
    wf = report["workflow_count"]
    st = report["stack_count"]

    top_wf = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in wf[:3]) or "—"
    top_st = ", ".join(f"[bold]{n}[/bold] ({c})" for n, c in st[:5]) or "—"

    body = (
        f"[bold cyan]{total}[/bold cyan] n8n jobs in the last "
        f"[bold]{days}[/bold] days\n\n"
        f"[bright_black]Top workflows:[/bright_black] {top_wf}\n"
        f"[bright_black]Top stacks:   [/bright_black] {top_st}\n\n"
        f"[bright_black]Unclassified industry: {report['unclassified_industry']}  ·  "
        f"unclassified workflow: {report['unclassified_workflow']}[/bright_black]"
    )
    console.print(Panel(body, title="n8n Demand · Snapshot",
                        border_style="cyan", padding=(1, 2)))


def _render_workflow_demand(report: dict):
    items = report["workflow_count"]
    if not items:
        return
    total = report["total_jobs"]
    stats = report["workflow_stats"]

    table = Table(
        title="Workflow Demand  ·  money & competition signals",
        box=box.ROUNDED, title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column("Workflow",  style="bold white", min_width=24, no_wrap=True)
    table.add_column("Jobs",      justify="right", style="cyan",   width=5)
    table.add_column("%",         justify="right", style="green",  width=4)
    table.add_column("Med $/hr",  justify="right", style="green",  width=9)
    table.add_column("Med Fixed", justify="right", style="yellow", width=10)
    table.add_column("Props",     justify="right", width=6)
    table.add_column("Verif",     justify="right", style="magenta", width=6)

    for name, count in items:
        s = stats[name]
        table.add_row(
            name,
            str(count),
            f"{round(count / total * 100)}",
            _fmt_money(s["med_hourly"], "/hr") if s["med_hourly"] else "—",
            _fmt_money(s["med_fixed"]) if s["med_fixed"] else "—",
            f"{s['avg_proposals']:.0f}" if s["avg_proposals"] else "—",
            f"{s['verified_pct']}%",
        )
    console.print(table)


def _render_industry_demand(report: dict):
    items = report["industry_count"]
    if not items:
        console.print(Panel(
            "[yellow]No industries detected.[/yellow] "
            "Most n8n posts don't mention a vertical explicitly.",
            border_style="yellow",
        ))
        return
    total = report["total_jobs"]

    table = Table(
        title="Industry Demand  ·  classified jobs only",
        box=box.ROUNDED, title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black", expand=True,
    )
    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Industry", style="bold white", ratio=1)
    table.add_column("Jobs",  justify="right", style="cyan",  min_width=5)
    table.add_column("Share", justify="right", style="green", min_width=6)
    table.add_column("Bar",   min_width=20)

    max_count = items[0][1] or 1
    for i, (name, count) in enumerate(items, 1):
        share = round(count / total * 100, 1)
        bar = "█" * max(1, int(count / max_count * 20))
        table.add_row(str(i), name, str(count), f"{share}%", f"[cyan]{bar}[/cyan]")
    console.print(table)


def _render_stack_demand(report: dict):
    items = report["stack_count"]
    if not items:
        return
    total = report["total_jobs"]

    table = Table(
        title="Stack / Tools Frequency  ·  pre-build for these integrations",
        box=box.ROUNDED, title_style="bold cyan",
        header_style="bold white on grey23",
        border_style="bright_black", expand=True,
    )
    table.add_column("#", justify="right", style="bright_black", min_width=3)
    table.add_column("Tool / Stack", style="bold white", ratio=1)
    table.add_column("Jobs", justify="right", style="cyan", min_width=5)
    table.add_column("% of n8n posts", justify="right", style="green", min_width=14)
    table.add_column("Bar", min_width=18)

    max_count = items[0][1] or 1
    for i, (name, count) in enumerate(items[:20], 1):
        share = round(count / total * 100, 1)
        bar = "█" * max(1, int(count / max_count * 18))
        table.add_row(str(i), name, str(count), f"{share}%", f"[magenta]{bar}[/magenta]")
    console.print(table)


def _render_matrix(report: dict):
    matrix = report["matrix"]
    if not matrix:
        return
    industries = [i for i, _ in report["industry_count"]]
    workflows  = [w for w, _ in report["workflow_count"]]

    # Drop empty rows/cols and trim to top 7 each (fits standard terminals)
    industries = [i for i in industries if any(matrix[i].values())][:8]
    workflows  = [w for w in workflows
                  if any(matrix[i].get(w, 0) for i in industries)][:6]
    if not industries or not workflows:
        return

    table = Table(
        title="Industry × Workflow  ·  where each vertical is investing",
        box=box.SIMPLE_HEAVY, title_style="bold cyan",
        header_style="bold white on grey23", border_style="bright_black",
        show_lines=False, pad_edge=False,
    )
    table.add_column("Industry", style="bold white", min_width=22, no_wrap=True)
    for wf in workflows:
        table.add_column(_abbr_wf(wf), justify="center", width=8, no_wrap=True)

    for ind in industries:
        row = [ind]
        for wf in workflows:
            val = matrix[ind].get(wf, 0)
            row.append(_color_cell(val))
        table.add_row(*row)
    console.print(table)
    legend = "  ".join(f"[bright_black]{_abbr_wf(w)}[/bright_black]=[white]{w}[/white]"
                       for w in workflows)
    console.print(f"  {legend}\n")


def _render_top_opportunities(report: dict):
    jobs = report["jobs"][:12]
    if not jobs:
        return
    table = Table(
        title="Top Opportunities  ·  budget × verified × low competition",
        caption="Full list w/ industry · stacks · country in: exports/n8n_demand_latest.xlsx",
        caption_style="bright_black",
        box=box.ROUNDED, title_style="bold yellow",
        header_style="bold white on grey23",
        border_style="bright_black",
    )
    table.add_column("#",        justify="right", style="bold yellow", width=3)
    table.add_column("Title",    style="white", min_width=36, no_wrap=True)
    table.add_column("Workflow", style="cyan",    width=14, no_wrap=True)
    table.add_column("Budget",   justify="right", style="green", width=11, no_wrap=True)
    table.add_column("Props",    justify="right", width=6)

    for i, j in enumerate(jobs, 1):
        wf  = ", ".join(j["workflows"][:1])  or "—"
        verified = " [green]✓[/green]" if j["client_verified"] else ""
        table.add_row(
            str(i),
            _short(j["title"], 36),
            _short(wf, 14),
            _budget_label_short(j),
            str(j["total_applicants"]) + verified,
        )
    console.print(table)


# ─── Excel export ────────────────────────────────────────────────────────────

def export(report: dict, days: int) -> str:
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = os.path.join(EXPORTS_DIR, f"n8n_demand_{ts}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)

    _xl_summary(wb, report, days)
    _xl_workflow(wb, report)
    _xl_industry(wb, report)
    _xl_stack(wb, report)
    _xl_matrix(wb, report)
    _xl_jobs(wb, report)

    wb.save(path)
    wb.save(os.path.join(EXPORTS_DIR, "n8n_demand_latest.xlsx"))
    return path


_HDR_FILL = PatternFill("solid", fgColor="1F3864")
_HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
_BOLD     = Font(bold=True, size=10)
_NORMAL   = Font(size=10)
_CENTER   = Alignment(horizontal="center", vertical="center")
_LEFT     = Alignment(horizontal="left",   vertical="center", wrap_text=True)
_RIGHT    = Alignment(horizontal="right",  vertical="center")


def _xl_header(ws, headers, widths):
    for c, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = _CENTER
        ws.column_dimensions[get_column_letter(c)].width = w


def _xl_summary(wb, report, days):
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 50
    rows = [
        ("Window",                 f"Last {days} days"),
        ("Total n8n jobs",         report["total_jobs"]),
        ("Top workflow",           report["workflow_count"][0][0] if report["workflow_count"] else "—"),
        ("Top industry",           report["industry_count"][0][0] if report["industry_count"] else "—"),
        ("Top stack",              report["stack_count"][0][0]    if report["stack_count"]    else "—"),
        ("Unclassified industry",  report["unclassified_industry"]),
        ("Unclassified workflow",  report["unclassified_workflow"]),
    ]
    for i, (k, v) in enumerate(rows, 1):
        ws.cell(row=i, column=1, value=k).font = _BOLD
        ws.cell(row=i, column=2, value=v).font = _NORMAL


def _xl_workflow(wb, report):
    ws = wb.create_sheet("By Workflow")
    headers = ["#", "Workflow", "Jobs", "Share %",
               "Med Hourly $", "Med Fixed $", "Avg Proposals", "Verified %"]
    widths  = [4, 28, 7, 9, 13, 13, 13, 11]
    _xl_header(ws, headers, widths)

    total = report["total_jobs"]
    for i, (name, count) in enumerate(report["workflow_count"], 1):
        s = report["workflow_stats"][name]
        r = i + 1
        ws.cell(row=r, column=1, value=i).alignment = _CENTER
        ws.cell(row=r, column=2, value=name).font = _BOLD
        ws.cell(row=r, column=3, value=count).alignment = _RIGHT
        ws.cell(row=r, column=4, value=round(count / total * 100, 1) if total else 0).alignment = _RIGHT
        ws.cell(row=r, column=5, value=round(s["med_hourly"], 2) if s["med_hourly"] else None).alignment = _RIGHT
        ws.cell(row=r, column=6, value=round(s["med_fixed"], 2)  if s["med_fixed"]  else None).alignment = _RIGHT
        ws.cell(row=r, column=7, value=round(s["avg_proposals"], 1) if s["avg_proposals"] else None).alignment = _RIGHT
        ws.cell(row=r, column=8, value=s["verified_pct"]).alignment = _RIGHT
    last = len(report["workflow_count"]) + 1
    if last > 1:
        ws.conditional_formatting.add(
            f"C2:C{last}",
            ColorScaleRule(start_type="min", start_color="FFFFFF",
                           end_type="max", end_color="4472C4"),
        )
    ws.freeze_panes = "A2"


def _xl_industry(wb, report):
    ws = wb.create_sheet("By Industry")
    _xl_header(ws, ["#", "Industry", "Jobs", "Share %"], [4, 28, 8, 10])
    total = report["total_jobs"]
    for i, (name, count) in enumerate(report["industry_count"], 1):
        r = i + 1
        ws.cell(row=r, column=1, value=i).alignment = _CENTER
        ws.cell(row=r, column=2, value=name).font = _BOLD
        ws.cell(row=r, column=3, value=count).alignment = _RIGHT
        ws.cell(row=r, column=4, value=round(count / total * 100, 1) if total else 0).alignment = _RIGHT
    ws.freeze_panes = "A2"


def _xl_stack(wb, report):
    ws = wb.create_sheet("By Stack")
    _xl_header(ws, ["#", "Tool / Stack", "Jobs", "Share %"], [4, 28, 8, 10])
    total = report["total_jobs"]
    for i, (name, count) in enumerate(report["stack_count"], 1):
        r = i + 1
        ws.cell(row=r, column=1, value=i).alignment = _CENTER
        ws.cell(row=r, column=2, value=name).font = _BOLD
        ws.cell(row=r, column=3, value=count).alignment = _RIGHT
        ws.cell(row=r, column=4, value=round(count / total * 100, 1) if total else 0).alignment = _RIGHT
    ws.freeze_panes = "A2"


def _xl_matrix(wb, report):
    ws = wb.create_sheet("Industry x Workflow")
    industries = [i for i, _ in report["industry_count"]]
    workflows  = [w for w, _ in report["workflow_count"]]
    industries = [i for i in industries if any(report["matrix"][i].values())]
    workflows  = [w for w in workflows
                  if any(report["matrix"][i].get(w, 0) for i in industries)]
    if not industries or not workflows:
        return

    ws.cell(row=1, column=1, value="Industry \\ Workflow").fill = _HDR_FILL
    ws.cell(row=1, column=1).font = _HDR_FONT
    ws.column_dimensions["A"].width = 26
    for c, wf in enumerate(workflows, 2):
        cell = ws.cell(row=1, column=c, value=wf)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = _CENTER
        ws.column_dimensions[get_column_letter(c)].width = 18
    for r, ind in enumerate(industries, 2):
        ws.cell(row=r, column=1, value=ind).font = _BOLD
        for c, wf in enumerate(workflows, 2):
            ws.cell(row=r, column=c, value=report["matrix"][ind].get(wf, 0)).alignment = _CENTER
    ws.freeze_panes = "B2"
    last_col = get_column_letter(len(workflows) + 1)
    ws.conditional_formatting.add(
        f"B2:{last_col}{len(industries) + 1}",
        ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                       mid_type="percentile", mid_value=50, mid_color="FFD966",
                       end_type="max", end_color="C00000"),
    )


def _xl_jobs(wb, report):
    ws = wb.create_sheet("Jobs")
    headers = ["Score", "Posted", "Title", "Industries", "Workflows", "Stacks",
               "Budget", "Proposals", "Country", "Verified", "Hires", "Job ID"]
    widths  = [6, 12, 60, 22, 28, 28, 14, 10, 14, 9, 7, 26]
    _xl_header(ws, headers, widths)

    for i, j in enumerate(report["jobs"], 1):
        r = i + 1
        ws.cell(row=r, column=1, value=round(j["opp_score"])).alignment = _CENTER
        ws.cell(row=r, column=2, value=(j["published_at"] or "")[:10]).alignment = _CENTER
        ws.cell(row=r, column=3, value=j["title"]).alignment = _LEFT
        ws.cell(row=r, column=4, value=", ".join(j["industries"])).alignment = _LEFT
        ws.cell(row=r, column=5, value=", ".join(j["workflows"])).alignment = _LEFT
        ws.cell(row=r, column=6, value=", ".join(j["stacks"])).alignment = _LEFT
        ws.cell(row=r, column=7, value=_budget_label(j)).alignment = _RIGHT
        ws.cell(row=r, column=8, value=j["total_applicants"]).alignment = _RIGHT
        ws.cell(row=r, column=9, value=j["client_country"]).alignment = _LEFT
        ws.cell(row=r, column=10, value="Yes" if j["client_verified"] else "No").alignment = _CENTER
        ws.cell(row=r, column=11, value=j["client_hires"]).alignment = _RIGHT
        ws.cell(row=r, column=12, value=j["id"]).alignment = _LEFT
    ws.freeze_panes = "A2"
    if report["jobs"]:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(report['jobs']) + 1}"
        ws.conditional_formatting.add(
            f"A2:A{len(report['jobs']) + 1}",
            ColorScaleRule(start_type="min", start_color="FFFFFF",
                           mid_type="percentile", mid_value=50, mid_color="FFE699",
                           end_type="max", end_color="70AD47"),
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _match(text: str, taxonomy_re: list) -> list:
    return [label for label, rx in taxonomy_re if rx.search(text)]


def _money_stats(jobs: list[dict]) -> dict:
    hourly = []
    fixed  = []
    proposals = []
    verified = 0
    for j in jobs:
        if j["budget_type"] == "HOURLY":
            mid = (j["budget_min"] + j["budget_max"]) / 2 if (j["budget_min"] and j["budget_max"]) \
                  else (j["budget_amount"] or 0)
            if mid > 0:
                hourly.append(mid)
        elif j["budget_type"] == "FIXED" and j["budget_amount"] > 0:
            fixed.append(j["budget_amount"])
        if j["total_applicants"] > 0:
            proposals.append(j["total_applicants"])
        if j["client_verified"]:
            verified += 1
    return {
        "med_hourly":    round(median(hourly), 1) if hourly else 0,
        "med_fixed":     round(median(fixed), 0)  if fixed  else 0,
        "avg_proposals": sum(proposals) / len(proposals) if proposals else 0,
        "verified_pct":  round(verified / len(jobs) * 100) if jobs else 0,
    }


def _opportunity_score(job: dict) -> float:
    """0–100 ranking proxy: budget × verified × low-competition."""
    if job["budget_type"] == "HOURLY":
        rate = (job["budget_min"] + job["budget_max"]) / 2 if (job["budget_min"] and job["budget_max"]) \
               else job["budget_amount"]
        budget_score = min(rate / 100, 1.0)            # $100/hr saturates
    elif job["budget_type"] == "FIXED" and job["budget_amount"] > 0:
        budget_score = min(job["budget_amount"] / 5000, 1.0)  # $5k saturates
    else:
        budget_score = 0.2                              # unknown — give a baseline

    verified_score = 1.0 if job["client_verified"] else 0.3
    props = job["total_applicants"] or 0
    competition_score = 1 / (1 + props / 15)            # 0 props → 1.0, 15 → 0.5

    return round(
        (budget_score * 0.50 + verified_score * 0.20 + competition_score * 0.30) * 100,
        1,
    )


_WF_ABBR = {
    "AI / LLM Agents":        "AI",
    "Voice / Telephony":      "Voice",
    "Lead Gen / Scraping":    "Leads",
    "Email Automation":       "Email",
    "CRM Sync":               "CRM",
    "Slack / Discord / Chat": "Chat",
    "Social Media Auto":      "Social",
    "Data Sync / ETL":        "ETL",
    "Document / PDF / OCR":   "Docs",
    "Webhook / API Glue":     "API",
    "Reporting / Analytics":  "Report",
    "Customer Support Bots":  "Support",
    "Calendar / Scheduling":  "Calend",
    "E-com Order Processing": "Orders",
}


def _abbr_wf(name: str) -> str:
    return _WF_ABBR.get(name, name[:6])


def _short(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_money(val: float, suffix: str = "") -> str:
    if not val:
        return "—"
    if val >= 1000:
        return f"${val/1000:.1f}k{suffix}"
    return f"${val:.0f}{suffix}"


def _budget_label(j: dict) -> str:
    if j["budget_type"] == "HOURLY":
        if j["budget_min"] and j["budget_max"]:
            return f"${j['budget_min']:.0f}-{j['budget_max']:.0f}/hr"
        if j["budget_amount"]:
            return f"${j['budget_amount']:.0f}/hr"
        return "Hourly"
    if j["budget_type"] == "FIXED" and j["budget_amount"]:
        return f"${j['budget_amount']:,.0f} fixed"
    return "—"


def _budget_label_short(j: dict) -> str:
    """Compact form for tight terminal columns."""
    if j["budget_type"] == "HOURLY":
        if j["budget_min"] and j["budget_max"]:
            return f"${j['budget_min']:.0f}-{j['budget_max']:.0f}/hr"
        if j["budget_amount"]:
            return f"${j['budget_amount']:.0f}/hr"
        return "hourly"
    if j["budget_type"] == "FIXED" and j["budget_amount"]:
        amt = j["budget_amount"]
        if amt >= 1000:
            return f"${amt/1000:.1f}k fxd"
        return f"${amt:.0f} fxd"
    return "—"


def _color_cell(val: int) -> str:
    if val == 0:
        return "[bright_black]·[/bright_black]"
    if val >= 10:
        return f"[bold red]{val}[/bold red]"
    if val >= 5:
        return f"[bold yellow]{val}[/bold yellow]"
    return f"[green]{val}[/green]"
