PY    := .venv/bin/python
TZ    ?= Asia/Kolkata
DAYS  ?= 14
N8N_DAYS ?= 7
LIMIT ?= 1000

.PHONY: setup auth seed dashboard skills shift watch fetch n8n n8n-local help

help:
	@echo ""
	@echo "  make setup                 — install dependencies (run once)"
	@echo "  make auth                  — authorize with Upwork (run once)"
	@echo "  make fetch                 — pull broad keyword sweep from Upwork"
	@echo "  make seed                  — load demo data (no API key needed)"
	@echo "  make dashboard             — full dashboard (skills + shift)"
	@echo "  make skills                — skills demand heatmap only"
	@echo "  make shift                 — volume heatmap + BD shift only"
	@echo "  make watch                 — dashboard auto-refresh every 30 min"
	@echo "  make n8n                   — fetch + analyze n8n jobs (default 7d)"
	@echo "  make n8n N8N_DAYS=14       — same, custom window"
	@echo "  make n8n-local             — re-analyze local DB only (no fetch)"
	@echo ""
	@echo "  Overrides: TZ=America/New_York  DAYS=7  N8N_DAYS=14  LIMIT=2000"
	@echo ""

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt
	cp -n .env.example .env 2>/dev/null || true
	@echo "  Done. Edit .env, then: make auth"

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
