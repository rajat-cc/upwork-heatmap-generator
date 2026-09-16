# Upwork Job Intelligence

> Terminal-based market intelligence for Upwork. A scheduled `sync` pulls your watched searches from the Upwork GraphQL API every two hours, classifies every job, keeps a time series of applicant counts, and purges fetched text after 24 hours. On top of that data: skills demand, client quality with hire rates, BD shift windows, and **automation demand by industry × workflow × stack** — so you know which automations to pre-build and pitch. Every table prints when its data was fetched.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
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
| **Volume Heatmap** | 7-day × 24-hour posting grid showing when clients post, in your timezone, plus the winnable-postings grid |
| **BD Shift Recommendation** | Best 8-hour window for your BD team, derived from peak posting volume |

### 2. n8n Automation Demand

| Axis | What it answers |
|---|---|
| **Workflows** (14 categories) | What does the automation *do*? AI/LLM Agents, CRM Sync, Voice/Telephony, Lead Gen, Email Automation, Webhook/API Glue, … |
| **Industries** (15 verticals) | Who is buying it? Healthcare, E-commerce, Real Estate, SaaS/B2B, Marketing/Agency, Logistics, … |
| **Stacks** (39 tools) | Which integrations recur? Zapier, OpenAI, Claude, Make, GoHighLevel, Vapi, Airtable, Supabase, … |

Each workflow gets money + competition signals (median $/hr, median fixed, avg proposals, % verified clients). Jobs are ranked by an opportunity score = **budget × verified × low-competition**.

### 3. Proposal funnel (closing the loop)

| Stage data | Where it comes from |
|---|---|
| **Notified / Filtered** | The proposal agent's `alerts.csv`, read in place (both column layouts) |
| **Drafted / Accepted** | The agent's `agent.db` (`drafted_ts`, `decided_ts`), read-only |
| **Submitted / Viewed / Interview / Hired / Lost** | You: `python main.py outcome <job> hired --bid 45 --connects 16` |

The funnel reports distinct jobs per stage, conversion between stages, a
Beta-shrunk win rate (flagged below five submissions), connects spent, cost per
hire, median submitted and winning bids, and all of it by category, client
segment, budget band, experience, hour notified, platform and workflow.

### 4. Price and score

| Lens | What it answers |
|---|---|
| **Bid bands** (`make bands`) | What the market pays: p25 / median / p75 by workflow × client segment × experience × budget type, rolling up to the workflow or budget-type band when a cell has fewer than 8 jobs, with your own submitted bids placed against the band and coloured by outcome |
| **Personal score** (`python main.py explain <job_id>`) | One 0–100 number per job built from six named components, every constant in `scoring.toml`, every component printed |
| **Market intel** (`make intel`) | `exports/market_intel_latest.json`: bands, demand by axis, watch suggestions, funnel summary. Aggregates only, no job ids or text, validated against `docs/market_intel.schema.json` before it is written |

### 5. Widen

| Lens / job | What it adds |
|---|---|
| **Automation** (`make automation`, `python main.py automation 7 --platform n8n Make`) | The n8n analysis across every platform label (n8n, Make, Zapier, GoHighLevel, Apps Script, Power Automate, Pipedream, Airtable Automations, custom code), with a platform table and a platform × workflow matrix. `n8n` stays as a shortcut |
| **LLM tagging** (inside `sync`) | Jobs the regexes leave without an industry are batched to `claude -p` on this machine (Claude Max, no API cost), labels validated against the taxonomy and stored with `source='llm'`. Off with `UPWORK_LLM_TAGGING=0`; capped per run. Needs the standalone CLI signed in (`claude auth login`); a signed-out CLI is reported in `status.json` and skipped |
| **Winnable heatmap** (`make shift`) | A second 7 × 24 grid of postings that had at most 10 applicants when first seen, in client segments you convert in; the shift window is derived from it once snapshots exist |
| **Clients** (`make clients`) | Likely repeat posters from a heuristic fingerprint (country, spend, hires, posted count), with their workflows, platforms and your history with them |
| **Weekly digest** (`make digest`, `make install-digest`) | This week vs last week: volume, platforms, workflows, hourly median, your funnel, sync health. Markdown to `exports/`, optionally to Telegram every Monday |

Excel exports for all lenses go to `exports/`.

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
make probe                       # what this key can query → docs/api_probe.json
make sync                        # fetch your watches, classify, snapshot, purge
make install-service             # …and keep doing that every 2 hours (launchd)
make dashboard
```

Watches (which searches `sync` runs) come from `UPWORK_WATCHES_FILE`, else the
proposal agent's `watches.txt` (`UPWORK_AGENT_DIR`), else `watch_terms.txt`,
else the single term `n8n`. Paste Upwork search URLs one per line as
`Label | <url>`; category and subcategory filters are applied server-side.

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

# Sync & retention
make sync                        # fetch → classify → snapshots → purge → ingest ledger → intel JSON → status.json
make sync-offline                # same path on a recorded page (no token needed)
make purge                       # dry-run of the 24h text purge (python main.py purge to apply)
make install-service             # launchd job: sync every 2 hours (uninstall-service removes it)
make install-digest              # launchd job: weekly digest, Monday 08:00 (uninstall-digest removes it)

# Proposal funnel
make ingest                      # read the agent's alerts.csv + agent.db into the outcome ledger
python main.py outcome <job id|url> submitted --bid 45 --bid-type hourly --connects 16
python main.py outcome <job id|url> hired    # also: viewed · interview · lost
make funnel                      # stages, shrunk win rate, cost per hire, by segment (FUNNEL_DAYS=30)
make outcomes                    # what you recorded recently

# Price & score
make bands                       # bid bands (p25/median/p75) by workflow × segment × experience, and your bids vs band
make intel                       # exports/market_intel_latest.json — aggregates only, for the proposal agent
python main.py explain <job_id>  # the score decomposition for one job (constants in scoring.toml)

# Widen
make automation                  # demand by platform (n8n, Make, Zapier, GHL, Apps Script, …), local DB
python main.py automation 7 --platform n8n Make   # fetch + analyze selected platforms
make clients                     # likely repeat clients (heuristic) and your history with them
make shift                       # posting volume + the winnable-postings grid and shift window
make digest                      # weekly digest to exports/ (and Telegram when configured)
make install-digest              # …every Monday 08:00 via launchd (uninstall-digest removes it)

# Data management
make fetch                       # one-off keyword sweep (8 themes)
make probe                       # which API queries/fields this key can use → docs/api_probe.json
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
python main.py fetch -k n8n -c <categoryUID> --since-days 2   # server-side filters
python main.py probe             # API capability report → docs/api_probe.json
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
├── fetcher.py               # GraphQL client: partial-data handling, token refresh, jittered backoff
├── seed.py                  # demo data generator
│
├── core/                    # shared building blocks
│   ├── models.py            #   Job, N8nReport, WorkflowStats dataclasses
│   ├── logging_setup.py     #   stdlib logging with Rich handler
│   ├── rich_helpers.py      #   Table factory, color cells, fmt_money
│   ├── probe.py             #   API capability probe (writes docs/api_probe.json)
│   ├── ratelimit.py         #   blocking token-bucket limiter (5 req/s)
│   ├── stats.py             #   median and p25–p75 band helpers
│   ├── watches.py           #   watches.txt parser (pasted Upwork search URLs)
│   ├── service.py           #   launchd LaunchAgent for the periodic sync
│   ├── lens.py              #   Lens protocol (analyze → render → export) + run_lens
│   ├── scoring.py           #   personal score from scoring.toml; `explain` decomposition
│   ├── capabilities.py      #   what the live probe says this key can query (gates optional fields)
│   └── xlsx_helpers.py      #   openpyxl header/style helpers
│
├── taxonomies/              # domain knowledge (what counts as what)
│   ├── compile.py           #   word-boundary regex compiler
│   ├── n8n.py               #   INDUSTRIES, WORKFLOWS, STACKS, abbreviations
│   ├── automation.py        #   PLATFORMS axis (n8n, Make, Zapier, GHL, Apps Script, …)
│   ├── countries.py         #   country spellings → ISO 3166-1 alpha-2
│   └── skills.py            #   skill name canonicalization (TECH_SKILLS, slug map)
│
├── features/                # one subpackage per analysis lens
│   ├── dashboard/           #   skills + clients + volume + shift
│   │   ├── analyzer.py
│   │   ├── renderer.py
│   │   ├── exporter.py
│   │   └── api.py           #   public: run() / run_skills_only / run_shift_only
│   ├── sync/                #   scheduled refresh (fetch → classify → snapshot → purge → ingest → status)
│   │   └── api.py
│   ├── funnel/              #   proposal funnel: ledger ingest, manual outcomes, funnel lens
│   │   ├── ingest.py        #   alerts.csv (two layouts) + agent.db + outcomes JSONL, idempotent
│   │   ├── outcomes.py      #   `outcome` command: DB row + monthly JSONL ledger
│   │   ├── analyzer.py      #   stages, Beta-shrunk win rate, connects economics, segments
│   │   ├── renderer.py / exporter.py
│   │   ├── vendor.py        #   vendorProposals → ledger: SUBMITTED/VIEWED/HIRED/LOST from your own proposals (gated on docs/api_probe.json)
│   │   └── api.py           #   FunnelLens (implements core.lens.Lens)
│   ├── bands/               #   bid bands lens (p25/median/p75 + your bids vs band)
│   ├── intel/               #   market_intel_latest.json producer + schema validator
│   ├── automation/          #   automation lens across platforms + `claude -p` industry tagger
│   ├── clients/             #   repeat-client accounts (heuristic fingerprint)
│   ├── digest/              #   weekly digest (Markdown + Telegram)
│   └── n8n/                 #   n8n automation demand
│       ├── classifier.py    #   regex tagging (4 axes) + opp_score + cache persist
│       ├── analyzer.py      #   cache-first load (FTS ∪ cached labels) + aggregate
│       ├── renderer.py      #   5 console tables
│       ├── exporter.py      #   6-sheet Excel
│       └── api.py           #   public: run(days, fetch, limit)
│
├── tests/                   # 133 tests: client, probe, sync, migrations, purge, ingest, funnel, scoring, bands, phase 4, …
│   └── fixtures/graphql/    #   recorded responses for offline runs (probe --offline, CI smoke)
├── .github/workflows/ci.yml # pytest + lint on every PR
├── scoring.toml             # every scoring constant, versioned
├── docs/                    # api_probe.json (live probe), market_intel.schema.json
├── pyproject.toml           # project metadata + pytest + ruff config
├── Makefile                 # one-line wrappers for everything
└── exports/                 # auto-generated .xlsx (git-ignored)
```

---

## Database schema

Single SQLite file (`upwork_jobs.db`), 8 tables, FTS5 search, versioned migrations (schema v8).

| Table | Purpose |
|---|---|
| `jobs` | One row per Upwork job (PK = Upwork id) |
| `job_skills` | Normalised skill list — `(job_id, skill)` for fast skill queries |
| `job_classifications` | Cached classifier output — `(job_id, axis, label, source, confidence)`, axis ∈ {industry, workflow, stack, platform}, `source ∈ {regex, llm, manual}`. Written on ingest, so analysis survives the text purge |
| `job_snapshots` | One row per observation of a job — `(job_id, observed_at, stage, source, total_applicants, hired_count, invites_sent, interviews, offers, unanswered_invites, job_status, avg_bid, avg_interviewed_bid, min_bid, max_bid, last_client_activity)`. `search` stages come free with every sync; `+2h`/`+24h`/`+72h` detail stages call `marketplaceJobPosting(id)` when `docs/api_probe.json` says the key may (`features/sync/snapshots.py`), later stages first, capped by `UPWORK_SNAPSHOT_DETAIL_CAP` per run |
| `proposal_events` | The outcome ledger — `(event_id, job_id, event, ts, source, bid_amount, bid_type, connects_spent, meta_json)`. Deterministic ids make every ingest idempotent; manual outcomes are also appended to `data/outcomes/*.jsonl` |
| `fetch_runs` | Every API fetch logged: search_term, started/finished, jobs_seen, jobs_new, status |
| `schema_meta` | Versioned migration ledger — each migration runs once, recorded with timestamp |
| `jobs_fts` (virtual) | FTS5 index over title + description + skills, kept in sync via triggers |

### Cohort-tracking columns on `jobs`
- `first_seen_at` — set once on first insert, **never overwritten** (true cohort timestamp)
- `last_fetched_at` — bumped on every refresh
- `fetch_count` — incremented on every refresh
- `discovered_via_search` — the watch label that originally found it
- `url` — `https://www.upwork.com/jobs/<id>` (derived once)
- `client_total_posted`, `hire_rate` — hires ÷ posted jobs, the strongest "will this post be filled" signal
- `purged_at` — set when the retention job blanked the text (a fresh fetch clears it)
- `client_company_id` — the client's public company id, learned from a detail-stage snapshot; the `clients` lens groups on it (`exact`) instead of the heuristic fingerprint

### Retention

Upwork's terms do not allow storing fetched data for more than 24 hours.
`sync` therefore blanks `title` and `description` on rows last fetched more than
`UPWORK_PURGE_TEXT_HOURS` (24) ago and stamps `purged_at`. Numbers, skill tags,
client fields, timestamps, snapshots and cached classifications stay, so every
lens still works on older rows (they show as `[purged] <id>`). `UPWORK_PURGE_FIELDS`
controls which of the two text fields are blanked.

Migrations run idempotently inside per-version SAVEPOINTs, so a partial failure cleanly leaves the DB at the last fully-applied version.

---

## How metrics are calculated

### Money figures

Every $ figure is a **median with its p25–p75 band** (`$25/hr (18–40)`).
Budgets are heavy-tailed; one $10k post would move an average a long way.

### Opportunity Score — general dashboard (per skill, 0-100)

```
demand_norm     = skill_jobs / max_jobs_across_all_skills
budget_norm     = median_hourly_or_fixed / max_median_budget_across_all_skills
competition     = 1 / (1 + median_proposals / 10)

opportunity_score = (demand_norm × 0.45 + budget_norm × 0.30 + competition × 0.25) × 100
```

### Personal opportunity score (per job, 0-100)

Six components, each normalised to 0–1 and weighted (`scoring.toml`, `[weights]`):

```
win_probability  shrunk win rate of the job's client segment from your funnel
                 (a prior per segment until 5 submissions exist there)
expected_value   budget ÷ cap ($100/hr or $5k fixed) × duration factor
hire_rate        client hires ÷ posted jobs (0.5 when unknown)
freshness        0.5 ^ (hours since publish / 24)
competition      1 / (1 + applicants / 15)
fit              overlap with UPWORK_PORTFOLIO_TAGS (0.5 when no file)

total = Σ weight × component × 100
```

`python main.py explain <job_id>` prints the input, normalised value, weight and
points of every component; every header and Excel summary carries the scoring
version so a number can always be traced to the formula that produced it.

### Bid bands
p25 / median / p75 of hourly midpoints and fixed amounts per (workflow, client
segment, experience, budget type). A cell needs `min_band_sample` (8) jobs;
below that it rolls up to the workflow band, then to the budget-type band, and
the row says which level it came from. Your SUBMITTED bids are placed against
the most specific band available: below p25, in band, or above p75.

### Trend % (general dashboard)
Compares the second half of the analysis window to the first half, counting only
jobs discovered within 48 h of being published (late discoveries reflect sweep
timing, not the market). Shown as `n/a` unless the window holds at least three
completed fetch runs and each half has at least 5 such jobs.

### Hire rate
`client_total_hires / client_total_posted`, per job, then the median per country
and overall. Missing when the client has never posted (or on rows fetched before
the column existed).

### Provenance
Every screen starts with `data as of <last successful fetch> (N h ago)` (red when
older than `UPWORK_STALE_AFTER_HOURS`) and ends with a footer: window, sample
size, fetch runs in the window, last fetch. The Excel exports carry the same
"data as of" in their title row / Summary sheet.

### Win rate and cost per hire
```
win_rate_raw    = hired / submitted
win_rate_shrunk = (hired + 1) / (submitted + 10)        # Beta(1, 9) prior: a 10% base rate
insufficient    = submitted < 5                          # shown as "n<5"
connects_spent  = Σ connects per SUBMITTED (UPWORK_DEFAULT_CONNECTS when not recorded)
cost_per_hire   = connects_spent × UPWORK_CONNECT_PRICE_USD / hired
```
Stage counts are distinct jobs (the agent logs duplicate alerts). Segments come
from the alert's own fields, so jobs the heatmap never fetched still slice.

### BD Shift Window
Sliding 8-hour sum over weekday posting volume, wrapped around midnight. The window with the highest sum wins.

### Client Quality Segments

| Segment | Criteria |
|---|---|
| Champion | Verified + ≥ $10k spent + ≥ 5 hires |
| Active | Verified + ≥ 1 hire |
| New | Verified, no previous hires |
| Risky | Not payment-verified |

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
| `UPWORK_AGENT_DIR` | `../upwork-proposal-agent` | Where the proposal agent lives; its `watches.txt` is reused read-only |
| `UPWORK_WATCHES_FILE` | — | Explicit watches file (overrides the agent's) |
| `UPWORK_SYNC_SINCE_DAYS` | `2` | Lookback per watch on each sync |
| `UPWORK_SYNC_LIMIT` | `300` | Max jobs per watch on each sync |
| `UPWORK_PURGE_TEXT_HOURS` | `24` | Blank fetched text after this many hours |
| `UPWORK_PURGE_FIELDS` | `title,description` | Which text fields the purge blanks |
| `UPWORK_STALE_AFTER_HOURS` | `24` | When the "data as of" line turns red |
| `UPWORK_EXPORTS_DIR` | `exports` | Where workbooks and `status.json` go |
| `UPWORK_CONNECT_PRICE_USD` | `0.15` | What one connect costs you |
| `UPWORK_DEFAULT_CONNECTS` | `12` | Connects assumed per submission when not recorded |
| `UPWORK_OUTCOMES_DIR` | `data/outcomes` | Portable JSONL ledger of manual outcomes |
| `UPWORK_SCORING_FILE` | `scoring.toml` | Alternative scoring constants file |
| `UPWORK_PORTFOLIO_TAGS` | — | One term per line; drives the score's `fit` component |
| `UPWORK_SNAPSHOT_DETAIL_CAP` | `300` | Detail-stage observations per sync run (one API call each) |
| `UPWORK_LLM_TAGGING` | `1` | Tag untagged industries via `claude -p` during sync (`0` disables) |
| `UPWORK_LLM_TAG_CAP` | `50` | Max jobs sent to the model per sync |
| `UPWORK_LLM_MODEL` | — | Optional `--model` for the CLI |
| `UPWORK_WINNABLE_MAX_APPLICANTS` | `10` | Applicant ceiling for the winnable grid |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Where the weekly digest is sent |

---

## Testing

```bash
make test         # 133 tests in ~3s
make test-cov     # with coverage report
make lint         # ruff check + format
```

Tests cover the regex taxonomies, the classifier behaviour against fixture jobs, the opportunity score formula across boundary inputs, migration idempotency on a fresh DB, and the GraphQL response parser.

CI runs on every PR against Python 3.11/3.12 plus a smoke test that exercises `seed → sync --offline → n8n -n → skills → dashboard → purge --dry-run → install-service --dry-run → probe --offline → ingest → outcome → funnel → bands → intel → explain → automation → clients → shift → digest` end-to-end without any API access.

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
