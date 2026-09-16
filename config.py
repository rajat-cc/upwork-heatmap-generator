"""Environment + infrastructure config only.

Domain taxonomies (skills, industries, etc.) live in `taxonomies/`.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(".env")

# ── OAuth / API ─────────────────────────────────────────────────────────────
CLIENT_ID = os.getenv("UPWORK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("UPWORK_CLIENT_SECRET", "")
ACCESS_TOKEN = os.getenv("UPWORK_ACCESS_TOKEN", "")
REDIRECT_URI = os.getenv("UPWORK_REDIRECT_URI", "http://localhost:8080/callback")

GRAPHQL_URL = "https://api.upwork.com/graphql"
AUTH_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
TOKEN_CACHE_FILE = ".token_cache.json"

# ── Storage ─────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("UPWORK_DB_PATH", "upwork_jobs.db")

# ── Display ─────────────────────────────────────────────────────────────────
TIMEZONE = os.getenv("TIMEZONE", "UTC")

# ── Fetcher tuning ──────────────────────────────────────────────────────────
# Max retries on transient errors (5xx, connection reset). Each retry waits
# RETRY_BACKOFF_BASE * (2 ** attempt) seconds, capped at RETRY_BACKOFF_MAX.
FETCH_MAX_RETRIES = int(os.getenv("UPWORK_FETCH_MAX_RETRIES", "4"))
FETCH_BACKOFF_BASE = float(os.getenv("UPWORK_FETCH_BACKOFF_BASE", "1.0"))
FETCH_BACKOFF_MAX = float(os.getenv("UPWORK_FETCH_BACKOFF_MAX", "30.0"))

# ── Sync, retention, exports ────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORTS_DIR = os.getenv("UPWORK_EXPORTS_DIR", "exports")

# The proposal agent's watches.txt is reused (read-only) so both tools watch
# the same population, which the funnel join in Phase 2 depends on.
AGENT_DIR = os.getenv("UPWORK_AGENT_DIR", os.path.join(ROOT_DIR, "..", "upwork-proposal-agent"))
WATCHES_FILE = os.getenv("UPWORK_WATCHES_FILE", "")
SYNC_SINCE_DAYS = int(os.getenv("UPWORK_SYNC_SINCE_DAYS", "2"))
SYNC_LIMIT_PER_WATCH = int(os.getenv("UPWORK_SYNC_LIMIT", "300"))

# Upwork's terms: data fetched from the API may not be stored beyond 24 hours.
# Text fields are blanked after this many hours; numbers, skill tags, client
# fields, timestamps and every derived row stay.
PURGE_TEXT_HOURS = int(os.getenv("UPWORK_PURGE_TEXT_HOURS", "24"))
PURGE_FIELDS = tuple(
    f.strip() for f in os.getenv("UPWORK_PURGE_FIELDS", "title,description").split(",") if f.strip()
)
STALE_AFTER_HOURS = int(os.getenv("UPWORK_STALE_AFTER_HOURS", "24"))
SNAPSHOT_MIN_GAP_HOURS = float(os.getenv("UPWORK_SNAPSHOT_MIN_GAP_HOURS", "2"))

# ── Funnel economics ────────────────────────────────────────────────────────
# What a connect costs you and how many a proposal spends when the outcome
# record does not say. Both are printed in the funnel header.
CONNECT_PRICE_USD = float(os.getenv("UPWORK_CONNECT_PRICE_USD", "0.15"))
DEFAULT_CONNECTS_PER_PROPOSAL = int(os.getenv("UPWORK_DEFAULT_CONNECTS", "12"))
# Manual outcomes are also appended here as a portable ledger (one file per month).
OUTCOMES_DIR = os.getenv("UPWORK_OUTCOMES_DIR", os.path.join(ROOT_DIR, "data", "outcomes"))

# ── LLM tagging (Phase 4) — `claude -p` on this machine, Claude Max ─────────
LLM_TAGGING = os.getenv("UPWORK_LLM_TAGGING", "1") not in ("0", "false", "no", "")
LLM_TAG_CAP = int(os.getenv("UPWORK_LLM_TAG_CAP", "50"))
LLM_MODEL = os.getenv("UPWORK_LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("UPWORK_LLM_TIMEOUT", "180"))

# ── Winnable-volume heatmap ─────────────────────────────────────────────────
WINNABLE_MAX_APPLICANTS = int(os.getenv("UPWORK_WINNABLE_MAX_APPLICANTS", "10"))

# ── Weekly digest delivery (optional; plain Telegram Bot API call) ──────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Backwards-compatibility shims ───────────────────────────────────────────
# Older modules may still import these from config; re-export until removed.
from taxonomies.skills import TECH_SKILLS  # noqa: E402,F401

CATEGORIES: list[str] = [
    "Web, Mobile & Software Dev",
    "IT & Networking",
    "Data Science & Analytics",
    "Engineering & Architecture",
    "Design & Creative",
    "Sales & Marketing",
    "Writing",
]

CONTRACTOR_TIERS: list[str] = ["ENTRY_LEVEL", "INTERMEDIATE", "EXPERT"]
