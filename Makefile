PYTHON := .venv/bin/python
TZ     ?= Asia/Kolkata
DAYS   ?= 14

.PHONY: setup auth seed dashboard skills shift watch fetch help

help:
	@echo ""
	@echo "  make setup      — install dependencies (run once)"
	@echo "  make auth       — authorize with Upwork (opens browser, run once)"
	@echo "  make fetch      — pull live data from Upwork API"
	@echo "  make seed       — load demo data (no API key needed)"
	@echo "  make dashboard  — full dashboard (skills + shift)"
	@echo "  make skills     — skills demand heatmap only"
	@echo "  make shift      — volume heatmap + BD shift only"
	@echo "  make watch      — dashboard that auto-refreshes every 30 min"
	@echo ""
	@echo "  Override timezone: make dashboard TZ=America/New_York"
	@echo "  Override window:   make skills DAYS=7"
	@echo ""

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt
	cp -n .env.example .env 2>/dev/null || true
	@echo ""
	@echo "  Done. Edit .env with your credentials, then run: make auth"
	@echo ""

auth:
	$(PYTHON) -c "from auth import run_auth_flow; token = run_auth_flow(); print('  Auth complete. Token saved. Run: make fetch')"

seed:
	$(PYTHON) main.py seed --days $(DAYS)

dashboard:
	$(PYTHON) main.py dashboard --timezone $(TZ) --days $(DAYS)

skills:
	$(PYTHON) main.py skills --days $(DAYS)

shift:
	$(PYTHON) main.py shift --timezone $(TZ) --days $(DAYS)

watch:
	$(PYTHON) main.py dashboard --timezone $(TZ) --days $(DAYS) --watch 30

fetch:
	$(PYTHON) main.py fetch --keywords python developer --limit 200
	$(PYTHON) main.py fetch --keywords react javascript frontend --limit 200
	$(PYTHON) main.py fetch --keywords machine learning AI LLM --limit 200
	$(PYTHON) main.py fetch --keywords aws devops kubernetes --limit 200
	$(PYTHON) main.py fetch --keywords data analyst SQL --limit 200
	$(PYTHON) main.py fetch --keywords node.js backend API --limit 200
	$(PYTHON) main.py fetch --keywords mobile flutter iOS android --limit 200
	$(PYTHON) main.py fetch --keywords blockchain solidity web3 --limit 200
