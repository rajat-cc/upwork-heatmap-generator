# Upwork Job Intelligence

> Terminal-based market intelligence for Upwork. Pulls live data from the Upwork GraphQL API, surfaces skills demand, client quality, BD shift windows, and **n8n automation demand by industry × workflow × stack** — so you know which automations to pre-build and pitch.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Rich](https://img.shields.io/badge/UI-Rich-blueviolet)
![SQLite](https://img.shields.io/badge/Storage-SQLite%20%2B%20FTS5-lightgrey?logo=sqlite)
![openpyxl](https://img.shields.io/badge/Export-Excel-217346?logo=microsoft-excel)
![Tests](https://img.shields.io/badge/tests-pytest-orange)

---

## What it does

Two analysis lenses over the same job database:

### 1. General Intelligence Dashboard

| Lens | What you see |
|---|---|
| **Skills Demand Heatmap** | Top 50 skills with trend, opportunity score, competition density, hourly/fixed budget breakdown |
| **Client Intelligence** | Quality segmentation (Champion / Active / New / Risky) and top countries with verified %, avg hires, avg budget |
| **Volume Heatmap** | 7-day × 24-hour posting grid showing when clients post, in your timezone |
| **BD Shift Recommendation** | Best 8-hour window for your BD team, derived from peak posting volume |

### 2. n8n Automation Demand

| Axis | What it answers |
|---|---|
| **Workflows** (14 categories) | What does the automation *do*? AI/LLM Agents, CRM Sync, Voice/Telephony, Lead Gen, Email Automation, Webhook/API Glue, … |
| **Industries** (15 verticals) | Who is buying it? Healthcare, E-commerce, Real Estate, SaaS/B2B, Marketing/Agency, Logistics, … |
| **Stacks** (39 tools) | Which integrations recur? Zapier, OpenAI, Claude, Make, GoHighLevel, Vapi, Airtable, Supabase, … |

Each workflow gets money + competition signals (median $/hr, median fixed, avg proposals, % verified clients). Jobs are ranked by an opportunity score = **budget × verified × low-competition**.

Excel exports for both lenses go to `exports/`.

---

## Quick start

```bash
# 1. Install
make setup                       # creates .venv + installs deps

# 2. Try without an API key (synthetic demo data)
make seed                        # 14 days of generated jobs
make dashboard                   # full intelligence dashboard

# 3. Connect to live Upwork data
cp .env.example .env             # then fill in UPWORK_CLIENT_ID/SECRET
make auth                        # one-time OAuth flow (opens browser)
make fetch                       # broad keyword sweep
make dashboard
```

---

## Commands

```bash
# Dashboard (skills + clients + volume + shift)
make dashboard                   # 14d window, default TZ
make dashboard DAYS=7 TZ=America/New_York
make watch                       # dashboard auto-refresh every 30 min

# Individual lenses
make skills                      # skills demand only
make shift                       # volume heatmap + BD shift only

# n8n automation demand (fetches from API by default)
make n8n                         # last 7 days
make n8n N8N_DAYS=14
make n8n N8N_DAYS=30 LIMIT=2000  # bigger pull
make n8n-local                   # skip API, re-analyze local DB only

# Data management
make fetch                       # broad keyword sweep (8 themes)
make backup                      # snapshot DB to backups/

# Development
make test                        # pytest
make test-cov                    # pytest + coverage
make lint                        # ruff check
```

Direct CLI also works (positional `days`, short flags):

```bash
python main.py n8n 7             # equivalent to: make n8n N8N_DAYS=7
python main.py dashboard 14 -t Asia/Kolkata
python main.py skills 30
python main.py fetch -k python ai -l 1000
```

---

## Sample output

**General dashboard (skills heatmap):**

```
SKILLS DEMAND HEATMAP  ·  Last 30 days
╭────┬──────────────────┬───────┬────────────┬─────────┬──────╮
│  # │ Skill            │  Jobs │ Demand     │   Trend │  Opp │
├────┼──────────────────┼───────┼────────────┼─────────┼──────┤
│  1 │ Python           │   822 │ ██████████ │  ↑ 222% │   51 │
│  2 │ JavaScript       │   667 │ ████████░░ │  ↑ 342% │   42 │
│  3 │ Machine Learning │   636 │ ████████░░ │  ↑ 309% │   40 │
╰────┴──────────────────┴───────┴────────────┴─────────┴──────╯
```

**n8n demand (snapshot + workflow money signals):**

```
╭─── n8n Demand · Snapshot ───╮
│ 686 n8n jobs in 30 days     │
│ Top workflows: AI/LLM (372),│
│   CRM Sync (314), ETL (235) │
│ Top stacks: Zapier (242),   │
│   Claude (220), GPT (219)   │
╰─────────────────────────────╯

Workflow Demand  ·  money & competition signals
╭──────────────────────┬──────┬─────┬──────────┬───────────┬───────┬───────╮
│ Workflow             │ Jobs │   % │ Med $/hr │ Med Fixed │ Props │ Verif │
├──────────────────────┼──────┼─────┼──────────┼───────────┼───────┼───────┤
│ AI / LLM Agents      │  372 │  54 │   $25/hr │      $300 │    37 │   92% │
│ CRM Sync             │  314 │  46 │   $28/hr │      $200 │    36 │   95% │
│ Voice / Telephony    │  122 │  18 │   $25/hr │      $400 │    33 │   87% │
│ Calendar / Scheduling│   57 │   8 │   $71/hr │      $450 │    31 │   86% │
╰──────────────────────┴──────┴─────┴──────────┴───────────┴───────┴───────╯
```

---

## Project layout

```
upwork_demand_analysis/
├── main.py                  # CLI dispatcher (thin)
├── config.py                # env config — URLs, DB path, fetcher tuning
├── auth.py                  # OAuth2 authorization-code flow
├── db.py                    # SQLite + FTS5 + versioned migrations
├── fetcher.py               # GraphQL client with retry/backoff
├── seed.py                  # demo data generator
│
├── core/                    # shared building blocks
│   ├── models.py            #   Job, N8nReport, WorkflowStats dataclasses
│   ├── logging_setup.py     #   stdlib logging with Rich handler
│   ├── rich_helpers.py      #   Table factory, color cells, fmt_money
│   └── xlsx_helpers.py      #   openpyxl header/style helpers
│
├── taxonomies/              # domain knowledge (what counts as what)
│   ├── compile.py           #   word-boundary regex compiler
│   ├── n8n.py               #   INDUSTRIES, WORKFLOWS, STACKS, abbreviations
│   └── skills.py            #   skill name canonicalization (TECH_SKILLS, slug map)
│
├── features/                # one subpackage per analysis lens
│   ├── dashboard/           #   skills + clients + volume + shift
│   │   ├── analyzer.py
│   │   ├── renderer.py
│   │   ├── exporter.py
│   │   └── api.py           #   public: run() / run_skills_only / run_shift_only
│   └── n8n/                 #   n8n automation demand
│       ├── classifier.py    #   regex tagging + opp_score + cache persist
│       ├── analyzer.py      #   FTS5 load + aggregate
│       ├── renderer.py      #   5 console tables
│       ├── exporter.py      #   6-sheet Excel
│       └── api.py           #   public: run(days, fetch, limit)
│
├── tests/                   # 32 tests covering classifier, taxonomies, migration, parser
├── .github/workflows/ci.yml # pytest + lint on every PR
├── pyproject.toml           # project metadata + pytest + ruff config
├── Makefile                 # one-line wrappers for everything
└── exports/                 # auto-generated .xlsx (git-ignored)
```

---

## Database schema

Single SQLite file (`upwork_jobs.db`), 6 tables, FTS5 search, versioned migrations.

| Table | Purpose |
|---|---|
| `jobs` | One row per Upwork job (PK = Upwork id) |
| `job_skills` | Normalised skill list — `(job_id, skill)` for fast skill queries |
| `job_classifications` | Cached classifier output — `(job_id, axis, label, source, confidence)` where `source ∈ {regex, llm, manual}`. Lets a future LLM pass merge in without recomputing |
| `fetch_runs` | Every API fetch logged: search_term, started/finished, jobs_seen, jobs_new, status |
| `schema_meta` | Versioned migration ledger — each migration runs once, recorded with timestamp |
| `jobs_fts` (virtual) | FTS5 index over title + description + skills, kept in sync via triggers |

### Cohort-tracking columns on `jobs`
- `first_seen_at` — set once on first insert, **never overwritten** (true cohort timestamp)
- `last_fetched_at` — bumped on every refresh
- `fetch_count` — incremented on every refresh
- `discovered_via_search` — the search term that originally found it
- `url` — `https://www.upwork.com/jobs/<id>` (derived once)

Migrations run idempotently inside per-version SAVEPOINTs, so a partial failure cleanly leaves the DB at the last fully-applied version.

---

## How metrics are calculated

### Opportunity Score — general dashboard (per skill, 0-100)

```
demand_norm     = skill_jobs / max_jobs_across_all_skills
budget_norm     = avg_hourly_or_fixed / max_budget_across_all_skills
competition     = 1 / (1 + avg_proposals / 10)

opportunity_score = (demand_norm × 0.45 + budget_norm × 0.30 + competition × 0.25) × 100
```

### Opportunity Score — n8n (per job, 0-100)

```
budget_score    = clamp(rate/$100  if hourly,
                        amount/$5k if fixed,
                        else 0.2,  to [0, 1])
verified_score  = 1.0 if client verified else 0.3
competition     = 1 / (1 + proposals / 15)

opp_score = (budget × 0.50 + verified × 0.20 + competition × 0.30) × 100
```

### Trend % (general dashboard)
Compares the second half of the analysis window to the first half. Suppressed (shown as `new`) when the older half has fewer than 5 data points.

### BD Shift Window
Sliding 8-hour sum over weekday posting volume, wrapped around midnight. The window with the highest sum wins.

### Client Quality Segments

| Segment | Criteria |
|---|---|
| Champion | Verified + ≥ $10k spent + ≥ 5 hires |
| Active | Verified + ≥ 1 hire |
| New | Verified, no previous hires |
| Risky | Unverified or 0 hires |

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `UPWORK_CLIENT_ID` | — | From your Upwork developer app |
| `UPWORK_CLIENT_SECRET` | — | From your Upwork developer app |
| `UPWORK_REDIRECT_URI` | `http://localhost:8080/callback` | Must match app settings |
| `TIMEZONE` | `UTC` | Default timezone for shift analysis |
| `UPWORK_DB_PATH` | `upwork_jobs.db` | SQLite file path |
| `UPWORK_FETCH_MAX_RETRIES` | `4` | Retry budget for transient API failures |
| `UPWORK_FETCH_BACKOFF_BASE` | `1.0` | Base seconds for exponential backoff |
| `UPWORK_FETCH_BACKOFF_MAX` | `30.0` | Cap on backoff delay |
| `UPWORK_INTEL_LOG` | `WARNING` | Log level (DEBUG/INFO/WARNING/ERROR) |

---

## Testing

```bash
make test         # 32 tests in ~0.3s
make test-cov     # with coverage report
make lint         # ruff check + format
```

Tests cover the regex taxonomies, the classifier behaviour against fixture jobs, the opportunity score formula across boundary inputs, migration idempotency on a fresh DB, and the GraphQL response parser.

CI runs on every PR against Python 3.10/3.11/3.12 plus a smoke test that exercises `seed → n8n -n → skills` end-to-end without any API access.

---

## Contributing

```bash
git checkout -b feature/your-thing
make setup-dev
make test
# ... do work ...
make test && make lint
```

Adding a new analysis lens?
1. Create `features/<your_lens>/` with `analyzer.py`, `renderer.py`, `exporter.py`, `api.py`
2. Wire `cmd_<your_lens>` in `main.py` and add a parser
3. Add a `make` target in the Makefile
4. Add tests under `tests/test_<your_lens>_*.py`

Adding a keyword to a taxonomy is safe — `taxonomies/n8n.py`. The compiler does the rest. The taxonomy tests will catch any malformed entry.

---

## License

MIT — see [LICENSE](LICENSE).
