# Simple helpers

.PHONY: venv install install-dev format lint typecheck check test test-cov audit bandit run dry-run init doctor doctor-live status status-json drain queue-status metrics docker-build docker-run build push release help \
	canary-probe promote-canary demote-canary promote-if-healthy show-version export-audit scheduler-safe scheduler-start scheduler-stop health pre-commit-install pre-commit-run verify-ingest send-test-email ui ui-alt ui-auto \
	guard-timeouts

venv:
	python3 -m venv .venv

install:
	. .venv/bin/activate && pip install -r requirements.txt

format:
	. .venv/bin/activate && ruff format .

install-dev:
	. .venv/bin/activate && pip install -r requirements-dev.txt

pre-commit-install:
	. .venv/bin/activate && pre-commit install

check:
	. .venv/bin/activate && ruff check . && mypy src && pytest -q

pre-commit-run:
	. .venv/bin/activate && pre-commit run --all-files

lint:
	. .venv/bin/activate && ruff check .

typecheck:
	. .venv/bin/activate && mypy src

test:
	mkdir -p tmp/pytest; \
	. .venv/bin/activate && TMPDIR=$$PWD/tmp pytest -q --basetemp="$$PWD/tmp/pytest"

test-cov:
	. .venv/bin/activate && pytest -q --cov=src --cov-report=term-missing --cov-report=html:reports/htmlcov

audit:
	. .venv/bin/activate && pip-audit -r requirements.txt -r requirements-dev.txt || true

bandit:
	. .venv/bin/activate && bandit -q -r src || true

run:
	. .venv/bin/activate && python -m src

dry-run:
	. .venv/bin/activate && python -m src --dry-run

init:
	. .venv/bin/activate && python -m src --init

doctor:
	. .venv/bin/activate && python -m src --doctor

doctor-live:
	. .venv/bin/activate && python -m src --doctor-live

status:
	. .venv/bin/activate && python -m src --status

status-json:
	. .venv/bin/activate && python -m src --status-json

drain:
	. .venv/bin/activate && python -m src --drain-queue

queue-status:
	. .venv/bin/activate && python -m src --queue-status

metrics:
	METRICS_PORT?=9108; \
	. .venv/bin/activate && METRICS_PORT=$$METRICS_PORT python -m src --dry-run

docker-build:
	docker build -t weather-lsa-control:latest .

docker-run:
	# Map health and metrics ports; mount secrets/ and data/ for persistence
	docker run --rm \
	  -e HEALTH_PORT=8080 -e METRICS_PORT=9108 \
	  -p 8080:8080 -p 9108:9108 \
	  -v $(PWD)/secrets:/app/secrets:ro \
	  -v $(PWD)/data:/app/data \
	  --name weather-lsa \
	  weather-lsa-control:latest

build:
	docker build -t $(IMAGE) .

push:
	docker push $(IMAGE)

help:
	@echo "Targets: venv, install, install-dev, format, lint, typecheck, check, test, run, dry-run, init, doctor, doctor-live, status, status-json, drain, queue-status, metrics, docker-build, docker-run, build, push, release, scheduler-safe, scheduler-start, scheduler-stop, health, verify-ingest, send-test-email, ui"

# Create a git tag and show changelog for the current version defined in pyproject.toml
release:
	@version=$$(python -c 'import tomllib,sys;print(tomllib.loads(open("pyproject.toml","rb").read()).get("project",{}).get("version","unknown"))'); \
	echo "Tagging v$$version"; \
	git tag -a v$$version -m "Release v$$version"; \
	git show v$$version --quiet; \
	echo; echo "Changelog:"; \
	grep -n "^## \[" CHANGELOG.md | head -n 5

# Canary/version management
canary-probe:
	. .venv/bin/activate && python -m src --doctor-ads-canary

promote-canary:
	. .venv/bin/activate && python -m src --promote-canary

demote-canary:
	. .venv/bin/activate && python -m src --demote-canary

promote-if-healthy:
	. .venv/bin/activate && python -m src --promote-if-healthy

show-version:
	. .venv/bin/activate && python -m src --show-version

# Audit export
export-audit:
	@days=$${DAYS:-7}; fmt=$${FMT:-jsonl}; \
	. .venv/bin/activate && python -m src --export-audit --days $$days --format $$fmt

# Scheduler (safe pilot)
scheduler-safe:
	. .venv/bin/activate && DRY_RUN=true GOOGLE_ADS_VALIDATE_ONLY=true python -m src --scheduler

# Start the scheduler in the background with optional ports (defaults: HEALTH_PORT=18080, METRICS_PORT=18081)
scheduler-start:
	mkdir -p tmp logs; \
	. .venv/bin/activate && \
	HEALTH_PORT=$${HEALTH_PORT:-18080}; METRICS_PORT=$${METRICS_PORT:-18081}; \
	DRY_RUN=$${DRY_RUN:-true}; GOOGLE_ADS_VALIDATE_ONLY=$${GOOGLE_ADS_VALIDATE_ONLY:-true}; \
	nohup env HEALTH_PORT=$$HEALTH_PORT METRICS_PORT=$$METRICS_PORT DRY_RUN=$$DRY_RUN GOOGLE_ADS_VALIDATE_ONLY=$$GOOGLE_ADS_VALIDATE_ONLY python -m src --scheduler > logs/scheduler.out 2>&1 & echo $$! > tmp/scheduler.pid; \
	echo "Scheduler started (pid $$(cat tmp/scheduler.pid)). Logs: logs/scheduler.out"

# Stop the background scheduler if running
scheduler-stop:
	@if [ -f tmp/scheduler.pid ]; then \
		PID=$$(cat tmp/scheduler.pid); \
		kill $$PID || true; \
		rm -f tmp/scheduler.pid; \
		echo "Scheduler stopped."; \
	else \
		echo "No scheduler pid file."; \
	fi

# Quick health check against the running endpoints (uses default ports unless overridden)
health:
	. .venv/bin/activate && python tools/health_check.py

# Guard: detect any direct requests.post without timeout
guard-timeouts:
	. .venv/bin/activate && python tools/check_requests_timeouts.py

# Live NWS ingest verifier (no Ads mutations)
verify-ingest:
	python tools/verify_ingest.py

# Quick email test
send-test-email:
	. .venv/bin/activate && python -m src --send-test-email

# Streamlit UI
ui:
	. .venv/bin/activate && streamlit run ui/app.py --server.headless=true

# Streamlit UI on alternate port to avoid conflicts with a running instance
ui-alt:
	. .venv/bin/activate && streamlit run ui/app.py --server.headless=true --server.port=8511

# Streamlit UI (auto-pick a free port 8510-8520)
ui-auto:
	. .venv/bin/activate && python tools/run_ui.py
