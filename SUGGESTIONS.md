# Repository audit and improvement plan

Date: 2025-08-16

## architecture map (high level)

- Runtime surfaces
  - CLI: `python -m src` (entry: `src/__main__.py`) with subcommands for doctor, scheduler, queue ops, exports.
  - Services: health and metrics HTTP servers (config via env).
  - UI: Streamlit multipage app under `ui/` with a Folium/Leaflet map and rich overlays.
- Core domains
  - Weather ingest and rules: `src/weather_monitor.py`, `src/rules.py`, `src/ratelimit.py`.
  - Ads/LSA integration: `src/lsa_client.py`, `src/lsa_reporting.py`, `src/worker.py`.
  - Persistence: SQLite in `data/app.db`, schema helpers in `src/db.py`.
  - Observability: `src/metrics.py`, `src/observability.py`, `src/tracing.py` (Sentry/Otel), logging setup in `src/__main__.py`.
  - Settings: `src/config/settings.py` (Pydantic Settings + env overrides).
  - Notifications: `src/notifier.py` (email channel).
  - UI layers and fetchers: `ui/map_layers.py`, `ui/http_client.py`, `ui/overlay_status.py`, pages in `ui/pages/`.

## dependency graph (selected)

```mermaid
flowchart LR
  subgraph CLI/Daemon
    MAIN[src/__main__.py] --> CFG[src/config/settings.py]
    MAIN --> OBS[src/observability.py]
    MAIN --> METRICS[src/metrics.py]
    MAIN --> DB[src/db.py]
    MAIN --> RULES[src/rules.py]
    MAIN --> WM[src/weather_monitor.py]
    MAIN --> WORKER[src/worker.py]
    WM --> LSA[src/lsa_client.py]
    WORKER --> LSA
    LSA --> SECRETS[src/secrets_manager.py]
  end
  subgraph UI
    UI[ui/Dashboard.py] --> PAGES[ui/pages/*]
    PAGES --> ML[ui/map_layers.py]
    PAGES --> HC[ui/http_client.py]
    PAGES --> OS[ui/overlay_status.py]
  end
  OBS --- SENTRY[(sentry-sdk)]
  METRICS --- PROM[(prometheus-client)]
  TRACING((OTel)) --- OBS
  SETTINGS((pydantic-settings)) --- CFG
  HTTPX[(httpx+cachetools)] --- HC
  ADS[(google-ads API via google-auth)] --- LSA
```

## key risks

- External API fragility: Google Ads API versioning and auth token refresh edge cases.
- Weather sources variability: schema/availability drift across overlays; rate limits.
- Scheduler/queue safety: accidental production mutations; quiet hours and kill switches must remain enforced.
- SQLite contention: background scheduler and worker processes could contend; lack of WAL/pragma tuning.
- UI performance: many overlay layers; large GeoJSON; risk of slow rendering on lower-end devices.
- Secrets handling: legacy plaintext tokens; risk of accidental commit under `secrets/` or `.env`.
- Incomplete typing in `src/` can hide subtle bugs; limited type coverage.

## prioritized improvement backlog

1. Stabilize Ads integration and mutation safety

- Impact: High | Effort: Medium
- Items:
  - Add end-to-end dry-run integration test that simulates alert → queue → mutate pipeline (validateOnly).
  - Harden canary promotion/demotion flows with explicit audit logs and idempotency.
  - Expand guards: enforce REQUIRED_CAMPAIGN_LABELS and LSA_ONLY in mutators.

1. Strengthen type safety baseline (to 70%+ public surface)

- Impact: High | Effort: Medium
- Items:
  - Add `py.typed`; annotate key public functions in `lsa_client.py`, `rules.py`, `db.py`.
  - Enable Pyright in CI (non-blocking initially), publish TYPECOVERAGE.md.

1. Security hygiene and secret scanning

- Impact: High | Effort: Low
- Items:
  - Add `detect-secrets` baseline and pre-commit hook; add `pip-audit` and `bandit` tasks and CI jobs.
  - Add SECRET_SCANNER.md with rotation guidance; scrub examples.

1. Observability and logging consistency

- Impact: Medium | Effort: Low
- Items:
  - Add structured JSON logging config via env toggle across modules; unify log record extras with context (request_id/job_id).
  - Add timing decorators for hot paths in rules evaluation and Ads calls.

1. Test coverage to 85% target with reports

- Impact: Medium | Effort: Medium
- Items:
  - Enable pytest-cov in tasks and CI; add HTML/XML reports; add coverage badge generation.
  - Add property-based tests for rules predicates and geo filters with Hypothesis.

1. CI gates and release polish

- Impact: Medium | Effort: Low
- Items:
  - Add lint/type/test/security stages with artifacts; keep lint/type non-blocking first pass.
  - Keep release job, add wheels build and twine check.

1. UI performance and A11Y pass

- Impact: Medium | Effort: Medium
- Items:
  - Add lazy/tiled fetch for heavy overlays, chunk large GeoJSON; document keyboard bindings and focus order; contrast checks.

## missing quality gates and concrete proposals

- Lint: Ruff already configured; add ruff-format and import sort on save (done). Keep `E`, `F`, `I`, `UP`, `N`, `Q`.
- Type: mypy present; add Pyright with `pyrightconfig.json` (present) and CI step; publish TYPECOVERAGE.md.
- Tests: add pytest-cov gates via `--cov=src --cov-fail-under=85` in `make test-cov` (not default in CI initially).
- Security: add `pip-audit` and `bandit` to Makefile and pre-commit; CI jobs upload SARIF optionally.
- Release: `release.yml` present; add build sdist+wheel and attach to release.

Config snippets (drop-in):

- pytest coverage (pyproject)

```toml
[tool.pytest.ini_options]
addopts = "-q -ra"
```

- Makefile targets (added):

```makefile
test-cov:  # pytest with coverage and HTML
  . .venv/bin/activate && pytest -q --cov=src --cov-report=term-missing --cov-report=html:reports/htmlcov

audit:
  . .venv/bin/activate && pip-audit -r requirements.txt -r requirements-dev.txt || true

bandit:
  . .venv/bin/activate && bandit -q -r src || true
```

- pre-commit (added hooks)

```yaml
- repo: https://github.com/PyCQA/bandit
  rev: 1.7.9
  hooks:
    - id: bandit
      args: ["-q", "-r", "src"]
- repo: https://github.com/Yelp/detect-secrets
  rev: v1.5.0
  hooks:
    - id: detect-secrets
```

## top 5 issues to open (content below)

Note: Opening GitHub issues requires repository access; since this environment cannot call GitHub APIs directly, copy the following into new issues. Labels: [area:ads], [area:types], [security], [observability], [testing].

1. Harden Ads mutation safety and canary flows [area:ads]

- Checklist:
  - [ ] Add end-to-end dry-run test for alert→queue→mutate
  - [ ] Enforce REQUIRED_CAMPAIGN_LABELS in mutators
  - [ ] Idempotent canary promote/demote with audit

1. Raise type coverage to 70% [area:types]

- Checklist:
  - [ ] Add `py.typed` and annotate `lsa_client.py`, `rules.py`, `db.py`
  - [ ] Enable Pyright in CI (non-blocking)
  - [ ] Publish TYPECOVERAGE.md baseline

1. Add secret scanning and security audits [security]

- Checklist:
  - [ ] Add detect-secrets baseline and pre-commit hook
  - [ ] Add pip-audit and bandit to Makefile and CI
  - [ ] Document rotation in SECRET_SCANNER.md

1. Add structured logging context and timers [observability]

- Checklist:
  - [ ] Introduce log extra fields (job_id, request_id)
  - [ ] Add timing decorators around hot paths
  - [ ] Env toggle for JSON vs console logs across modules

1. Reach 85% test coverage with property-based tests [testing]

- Checklist:
  - [ ] Add pytest-cov and HTML reports
  - [ ] Hypothesis tests for rules and geo filters
  - [ ] Coverage badge in README
