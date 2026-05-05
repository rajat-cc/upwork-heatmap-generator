PY        := .venv/bin/python
PIP       := .venv/bin/pip
TZ        ?= Asia/Kolkata
DAYS      ?= 14
N8N_DAYS  ?= 7
LIMIT     ?= 1000
BACKUPS_DIR ?= backups

.PHONY: help setup setup-dev auth seed dashboard skills shift watch fetch \
        n8n n8n-local test test-cov lint backup clean

help:
	@echo ""
	@echo "  make setup                 — install runtime dependencies (first time)"
	@echo "  make setup-dev             — install runtime + dev (pytest, ruff)"
	@echo "  make auth                  — authorize with Upwork (one-time)"
	@echo ""
	@echo "  make fetch                 — pull broad keyword sweep from Upwork"
	@echo "  make seed                  — load demo data (no API key needed)"
	@echo ""
	@echo "  make dashboard             — full dashboard (skills + clients + shift)"
	@echo "  make skills                — skills demand heatmap only"
	@echo "  make shift                 — volume heatmap + BD shift only"
	@echo "  make watch                 — dashboard auto-refresh every 30 min"
	@echo ""
	@echo "  make n8n                   — fetch + analyze n8n jobs (default 7d)"
	@echo "  make n8n N8N_DAYS=14       — same, custom window"
	@echo "  make n8n-local             — re-analyze local DB only (no fetch)"
	@echo ""
	@echo "  make test                  — run pytest"
	@echo "  make test-cov              — pytest with coverage report"
	@echo "  make lint                  — ruff check + format check"
	@echo "  make backup                — snapshot upwork_jobs.db to $(BACKUPS_DIR)/"
	@echo "  make clean                 — remove pyc/pycache + test cache"
	@echo ""
	@echo "  Overrides: TZ=America/New_York  DAYS=7  N8N_DAYS=14  LIMIT=2000"
	@echo ""

setup:
	python3 -m venv .venv
	$(PIP) install -q -r requirements.txt
	cp -n .env.example .env 2>/dev/null || true
	@echo "  Done. Edit .env, then: make auth"

setup-dev: setup
	$(PIP) install -q -e ".[dev]"

auth:
	$(PY) -c "from auth import run_auth_flow; run_auth_flow(); print('  Auth complete.')"

seed:        ; $(PY) main.py seed $(DAYS)
dashboard:   ; $(PY) main.py dashboard $(DAYS) -t $(TZ)
skills:      ; $(PY) main.py skills $(DAYS)
shift:       ; $(PY) main.py shift $(DAYS) -t $(TZ)
watch:       ; $(PY) main.py dashboard $(DAYS) -t $(TZ) -w 30
n8n:         ; $(PY) main.py n8n $(N8N_DAYS) -l $(LIMIT)
n8n-local:   ; $(PY) main.py n8n $(N8N_DAYS) -n

fetch:
	@for kw in "python developer" "react javascript frontend" \
	           "machine learning AI LLM" "aws devops kubernetes" \
	           "data analyst SQL" "node.js backend API" \
	           "mobile flutter iOS android" "blockchain solidity web3"; do \
		$(PY) main.py fetch -k $$kw -l 200; \
	done

test:
	$(PY) -m pytest tests/ -v

test-cov:
	$(PY) -m pytest tests/ --cov=. --cov-report=term-missing --cov-report=html

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .

backup:
	@mkdir -p $(BACKUPS_DIR)
	@cp upwork_jobs.db $(BACKUPS_DIR)/upwork_jobs_$$(date +%Y-%m-%d_%H%M).db
	@ls -lh $(BACKUPS_DIR)/upwork_jobs_*.db | tail -3
	@echo "  Backed up to $(BACKUPS_DIR)/"

clean:
	@find . -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' -o -name 'htmlcov' \) -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete
	@echo "  Cleaned."
