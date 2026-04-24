# Upwork Job Intelligence Dashboard

> A terminal-based market intelligence tool for freelancers and business development teams — pulls live data from the Upwork GraphQL API and surfaces skills demand, client quality signals, and optimal BD shift windows.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Rich](https://img.shields.io/badge/UI-Rich-blueviolet)
![SQLite](https://img.shields.io/badge/Storage-SQLite-lightgrey?logo=sqlite)
![openpyxl](https://img.shields.io/badge/Export-Excel-217346?logo=microsoft-excel)

---

## What it does

| Feature | Description |
|---|---|
| **Skills Demand Heatmap** | Ranks top 50 skills by job count with trend, opportunity score, competition density, and hourly/fixed budget breakdown |
| **Client Intelligence** | Quality segmentation (Champion / Active / New / Risky) and top client countries with verified %, avg hires, and avg budget |
| **Volume Heatmap** | 7-day × 24-hour posting grid showing when clients actually post jobs, localised to your timezone |
| **BD Shift Recommendation** | Identifies the best 8-hour window for your BD team to be active, based on peak posting volume |
| **Excel Export** | Generates a multi-sheet `.xlsx` workbook automatically after every fetch and dashboard run |
| **Demo mode** | Ships with a realistic data seed — no API key needed to try it out |

---

## Terminal output

```
─────────────── UPWORK JOB INTELLIGENCE DASHBOARD ────────────────
  1,345 jobs  ·  2026-03-12 → 2026-04-24  ·  TZ: Asia/Kolkata

╭────┬──────────────────┬───────┬────────────┬─────────┬──────┬──────────╮
│  # │ Skill            │  Jobs │ Demand     │   Trend │  Opp │  Compete │
├────┼──────────────────┼───────┼────────────┼─────────┼──────┼──────────┤
│  1 │ Python           │   349 │ ██████████ │  ↑ 631% │   51 │     33.9 │
│  2 │ JavaScript       │   272 │ ████████░░ │ ↑ 1311% │   40 │     37.1 │
│  3 │ Machine Learning │   238 │ ███████░░░ │ ↑ 3200% │   36 │     39.7 │
│ 19 │ Blockchain       │    75 │ ██░░░░░░░░ │  ↑ 100% │   20 │     14.1 │
╰────┴──────────────────┴───────┴────────────┴─────────┴──────┴──────────╯

CLIENT INTELLIGENCE
  Champion: 22%  ·  Active: 35%  ·  Risky: 43%
  US: 87% verified  ·  India: 46% verified

BD SHIFT RECOMMENDATION
  Recommended window: 18:00 – 01:59 (Asia/Kolkata)
  Peak hour: 00:00  ·  Busiest day: Thu
```

---

## Requirements

- Python 3.10+
- An [Upwork developer account](https://www.upwork.com/developer/keys/apply) with a registered OAuth2 app *(only needed for live data — demo mode works without it)*

---

## Quick start

### 1. Clone and set up

```bash
git clone https://github.com/your-username/upwork-job-intelligence.git
cd upwork-job-intelligence
make setup
```

This creates a `.venv` and installs all dependencies.

### 2. Try it with demo data (no API key needed)

```bash
make seed        # generates 14 days of realistic synthetic data
make dashboard   # open the full dashboard
```

### 3. Connect to the Upwork API

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

```dotenv
UPWORK_CLIENT_ID=your_client_id
UPWORK_CLIENT_SECRET=your_client_secret
UPWORK_REDIRECT_URI=http://localhost:8080/callback
TIMEZONE=Asia/Kolkata
```

Then run the one-time OAuth flow:

```bash
make auth
```

This opens a browser for Upwork login, captures the token, and saves it to `.token_cache.json` (never committed to git).

---

## Usage

### Fetch live data

```bash
make fetch
```

Runs 8 parallel keyword searches across Python, React, ML/AI, AWS/DevOps, Data, Node, Mobile, and Blockchain. You can customise the keywords in the `Makefile`.

Fetch a specific keyword manually:

```bash
python main.py fetch --keywords "django rest api" --limit 500
```

### Dashboard

```bash
make dashboard                          # default timezone from .env
make dashboard TZ=America/New_York      # override timezone
make dashboard DAYS=7                   # 7-day analysis window
make watch                              # auto-refresh every 30 min
```

### Individual views

```bash
make skills                             # skills heatmap only
make shift                              # volume heatmap + BD shift only
python main.py skills --days 7 --categories "Web, Mobile & Software Dev"
```

---

## Excel export

Every `fetch` and `dashboard` run automatically writes to the `exports/` folder:

```
exports/
├── latest.xlsx                          ← always has the most recent data
└── upwork_dashboard_2026-04-24_1159.xlsx
```

The workbook contains five sheets:

| Sheet | Contents |
|---|---|
| **Skills Demand** | Full skills table with conditional formatting — data bar on job count, color scale on opportunity score |
| **Client Quality** | Champion / Active / New / Risky breakdown with share % |
| **Client Countries** | Top countries with verified %, avg hires, avg budget — color-coded verified % |
| **Volume Heatmap** | 7 × 24 heat grid of avg jobs/hr — white → yellow → red |
| **BD Shift Summary** | Recommended shift window, peak hour, busiest/slowest day, 24h volume bar |

---

## How metrics are calculated

### Opportunity Score (0–100)

Normalised composite of demand, budget, and competition:

```
demand_norm      = skill_jobs / max_jobs_across_all_skills
budget_norm      = avg_hourly_or_fixed / max_budget_across_all_skills
competition_pen  = 1 / (1 + avg_proposals / 10)

opportunity_score = (demand_norm × 0.45 + budget_norm × 0.30 + competition_pen × 0.25) × 100
```

A high score means strong demand, good pay, and low competition — the sweet spot.

### Trend %

Compares the second half of the analysis window to the first half. Suppressed (shown as `new`) when the older half has fewer than 5 data points to avoid misleading percentages from sparse data.

### BD Shift Window

Identifies the best contiguous 8-hour window using a sliding-window sum over weekday posting volume, wrapped around midnight.

### Client Quality Segments

| Segment | Criteria |
|---|---|
| Champion | Payment verified + ≥ $10k total spent + ≥ 5 hires |
| Active | Payment verified + ≥ 1 hire |
| New | Payment verified, no previous hires |
| Risky | Unverified or 0 hires |

---

## Project structure

```
upwork-job-intelligence/
├── main.py           # CLI entry point — subcommands: seed, fetch, skills, shift, dashboard
├── fetcher.py        # Upwork GraphQL API client with OAuth2 + pagination
├── analyzer.py       # All analytics: skills stats, client stats, volume matrix, shift recommendation
├── display.py        # Rich terminal renderer — tables, heatmaps, panels
├── exporter.py       # Excel workbook builder (openpyxl)
├── db.py             # SQLite layer with WAL mode and auto-migration
├── auth.py           # OAuth2 authorization code flow
├── seed.py           # Demo data generator (no API key needed)
├── config.py         # Env config, skill lists, categories
├── Makefile          # Developer shortcuts
├── requirements.txt
├── .env.example
└── exports/          # Auto-generated Excel files (git-ignored)
```

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `UPWORK_CLIENT_ID` | — | From your Upwork developer app |
| `UPWORK_CLIENT_SECRET` | — | From your Upwork developer app |
| `UPWORK_REDIRECT_URI` | `http://localhost:8080/callback` | Must match your app settings |
| `TIMEZONE` | `UTC` | Default timezone for shift analysis |

---

## Makefile reference

```bash
make setup      # Create .venv and install dependencies
make auth       # Run OAuth2 flow (one-time)
make seed       # Generate demo data (no API key needed)
make fetch      # Pull live jobs from Upwork API
make dashboard  # Full intelligence dashboard
make skills     # Skills heatmap only
make shift      # Volume heatmap + BD shift only
make watch      # Dashboard with 30-min auto-refresh
```

Override defaults:

```bash
make dashboard TZ=America/Chicago DAYS=30
make skills DAYS=7
```

---

## Contributing

Contributions are welcome. To get started:

1. Fork the repo and create a feature branch
2. Run `make setup` to install dependencies
3. Use `make seed` for local testing (no API key needed)
4. Open a pull request with a clear description of the change

Ideas for contribution:
- Additional skill normalisation mappings in `analyzer.py`
- Budget percentile (P25/P75) display using stored `budget_min` / `budget_max`
- Premium and Enterprise job sub-table
- Scheduled fetch via cron or GitHub Actions
- Export to Google Sheets

---

## License

MIT — see [LICENSE](LICENSE) for details.
