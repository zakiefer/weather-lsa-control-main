# Weather-Based LSA Control System

Automatically controls Google Ads campaigns (LSA workflow) based on NWS severe weather alerts.

## Setup

1. Create a virtual environment (at repo root):

- `python3 -m venv .venv`
- `source .venv/bin/activate`

1. Install dependencies:

- Runtime: `pip install -r requirements.txt`
- Dev (tests, tooling): `pip install -r requirements-dev.txt`
- Optional package install for CLI entrypoint: `pip install -e .`
- Or use Makefile helpers: `make venv && make install && make install-dev`

1. Copy env template and fill in IDs/tokens:

- `cp .env.example .env`

1. Place your Google OAuth client secret JSON in `secrets/client_secret.json`.

- The app will migrate/standardize from other locations if found.

1. Authenticate once (opens browser):

- `python -m src`
- Or after editable install: `weather-lsa`

## Run

- Dry-run (no Ads changes):
  - `python -m src --dry-run`
- Simulate an alert:
  - `python -m src --dry-run --force-alert`
- Optional radius filter:
  - `python -m src --dry-run --center-lat 37.9716 --center-lon -87.5711 --max-distance-mi 40`
- Initialize project structure:
  - `python -m src --init`
- Validate env, IDs, and secrets without network calls:
  - `python -m src --doctor`

## Streamlit UI

- Launch the dashboard UI (simplified page names, built-in sidebar navigation):

```bash
streamlit run ui/Dashboard.py
```

- Open <http://localhost:8510> (configured in `.streamlit/config.toml`).
- Pages: Dashboard, Live, Map, History, Ads, Reports, Settings, Health, Profile.
- Share links: Live/Map pages show a “Show link” option that generates a short `?page=...` URL you can copy.

### Map deep-linking and persistence

- The Map page stores UI state in both URL query params (for shareable links) and browser storage (for sticky preferences). On first load per tab, saved prefs are merged into the URL and the page reloads once. A reset button clears everything.
- Reset map preferences: Map sidebar > Options > "Reset map preferences". It clears stored keys and resets the URL to `?page=Map`.

Deep-link params (compact keys):

- st: CSV of states (e.g., IN,IL,KY)
- base: Basemap (Light|Dark|OSM|Satellite)
- cat: CSV of alert categories (severe,flood,tropical,winter,marine,other)
- ev: Event filter text; tg: Triggered-only toggle; tr: Trigger rule filter (0/1); tf: Trigger filter-types (0/1); az: Auto-zoom (0/1); fm: Fast mode (0/1)
- rd: Radar on (0/1); rs: Radar source (iem|rv); ro: Radar opacity (10-100); rah: Hide live radar when timeline shown (0/1)
- ht: Show historical alerts (0/1); hh: Hours back; hs: History selection; hr: Animate timeline (0/1)
- ra: Show radar archive (0/1); ram: Minutes back seed; rts: Timeline speed (float); rll: Loop (0/1); rtl: Show timeline controls (0/1)
- sat: GOES truecolor (0/1); sati: GOES IR (0/1); glm: GLM lightning (0/1); spc: SPC outlook (0/1); spcd: SPC day (1|2|3)
- eq: Earthquakes (0/1); eqmin: Minimum magnitude (float); trp: Tropics (0/1); wf: Wildfire (0/1)
- lat, lon, z: Map center lat/lon and zoom

Persisted browser keys

- localStorage: radar_on, radar_source, rv_opacity, ra_hide_live, sat_true, sat_ir, sat_opacity, glm_on, glm_opacity, spc_on, spc_day, basemap, cat_filters, states, eq_on, eq_minmag, trp_on, wf_on
- sessionStorage (internal flags to avoid loops): radar_prefs_applied, ui_prefs_applied

Example deep-link

- <http://localhost:8510/?page=Map&st=IN,IL,KY&base=Dark&rd=1&rs=rv&ro=70&rah=0&sat=1&sati=1&glm=1&spc=1&spcd=2&ra=1&ram=20&rts=1.5&rll=0&rtl=0&lat=38.3&lon=-87.6&z=7>

### Reduce flashing (UX)

- Client-side controls: The Map defaults to “Reduce flashing (client-side toggles)”. Opacity sliders and common layer toggles update in-map directly without forcing a Streamlit rerun.
- Batching: When you disable Reduce flashing, high-churn sidebar controls are grouped in small forms with Apply buttons to batch state changes into a single rerun.
- URL dedupe + throttle: Deep-link query parameter updates are deduplicated and throttled (~750 ms) to avoid redundant reruns and history spam during panning/zooming or rapid tweaks. Map center/zoom updates use the same guard.

### New in this build

- Resilient data fetching with retry/backoff and TTL caches (httpx + cachetools).
- In-app cache management and data source status panels (Settings > Data caches & sources; Map > Advanced).
- Overlay health pips next to layer toggles (green/amber/red/gray) showing freshness and last status.
- Unified Layer Opacity drawer with client-side updates and deep-link sync.

### Examples

- Scheduler with region mappings disabled (use default Google Ads IDs):

  - USE_REGION_MAPPINGS=false python -m src --scheduler
- One-off run with mappings disabled via CLI:

  - python -m src --no-region-mappings --dry-run
- Force an alert and validate-only mutate gate:

  - python -m src --force-alert --validate-only

## Operations runbook

- See docs/RUNBOOK.md for:

  - Kill switch usage
  - Clearing storm holds
  - Re-authentication
  - Secret rotation
  - Interpreting common error codes/messages

### Scheduler mode (hands-off)

- Run periodic weather checks and drain the mutation queue with a singleton lock:

  - python -m src --scheduler
- Enable health and metrics exporters (set in env or .env):

  - HEALTH_PORT=8080 METRICS_PORT=9108 python -m src --scheduler
- Endpoints:

  - Liveness: GET /healthz
  - Readiness: GET /readyz (returns 200 when ready, 503 otherwise)
  - Metrics: GET /metrics (Prometheus format)

## CLI flags

- Core: --dry-run, --validate-only, --force-alert, --force-event
- Radius: --center-lat, --center-lon, --max-distance-mi
- Utilities: --list-campaigns, --create-test-account, --init, --doctor, --clear-hold, --log-level

  - Logging: --json-logs, and --log-level for verbosity control
  - Preflight: --doctor-live for live-run readiness checks
  - Notifications: --notify-test to send a one-off test email
  - Scheduler: --scheduler (periodic jobs)
  - CAP browsing: --caps, --caps-limit N (list latest N CAP alerts)
- Ads OAuth token: loads token.json if present and refreshes if expired (non-interactive; does not open a browser).
- DB probe: ensures schema and performs a test write to a temporary _doctor table.
- Figma MCP (HTTP) tool ID compatibility:

  - figma.ping → figma_ping
  - figma.auth → figma_auth
  - figma.me → figma_me
  - figma.file.get → figma_file_get
  - figma.file.nodes → figma_file_nodes

- Clock skew check: compares system time to an external HTTP Date header and warns if skew > 120 seconds.

- Notifications (email) can be enabled via .env (see Notification section below).

### Profiles and typed settings

- Profiles: set PROFILE to dev, staging, or prod.

  - In prod, the app refuses to start with conflicting flags (e.g., DRY_RUN=true, FORCE_ALERT=true, or CREATE_TEST_ACCOUNT=true).
- Settings are validated with Pydantic; ID fields must be digits-only (no dashes). Clear error messages are shown on invalid configurations.

### Secrets management

- The app can store sensitive values in the OS keychain (via keyring). On startup, it attempts to migrate:

  - Google Ads developer token (developer_token)
  - SMTP password (smtp_password)
- Environment overrides are supported with SECRET_* variables (e.g., SECRET_DEVELOPER_TOKEN, SECRET_SMTP_PASSWORD).

- See CONTRIBUTING.md for naming conventions (enforced by ruff’s pep8-naming).

### Pre-commit hooks (optional)

## Tests

- Install dev deps: pip install -r requirements-dev.txt
- Run: pytest -q
- Tests cover FIPS/name matching, filters, radius behavior, rollback, breaker, quiet hours, and golden flows. Fixtures isolate DB and logs to temp dirs.

Note: make test runs pytest with a repo-local temp directory (tmp/pytest) to avoid intermittent macOS tmp space issues.

### CI

- GitHub Actions workflow runs tests on pushes/PRs to main/master (.github/workflows/ci.yml).
- Type-check: mypy (config in pyproject.toml)
- Unit tests: pytest
- Doctor-live: pipeline fails if doctor-live reports FAIL
- Docker build: always builds image

## Local MCP: Hugging Face server

We include a small, local-only HTTP MCP server at `tools/hf_mcp_server.py` (binds to 127.0.0.1:3865 by default).

Tools available:

- hf_sentiment(text[, model_id])
- hf_embeddings(text[, model_id])
- hf_summarize(text[, model_id, max_new_tokens, min_new_tokens])
- hf_zero_shot(text, labels[, model_id, multi_label, debug])
- hf_generate(text[, model_id, max_new_tokens, temperature, top_p, do_sample])
- hf_translate(text[, src, tgt, model_id, max_length, num_beams, min_new_tokens])

Health endpoints:

- GET /health → { ok: true }
- GET /healthz → includes cache sizes/models and transformers/torch versions

Behavior and env toggles:

- Offline: set HF_HUB_OFFLINE=1 to use local cache only (no downloads).
- Zero-shot debug: set ZERO_SHOT_DEBUG=1 (or pass debug:true) to include labels/scores in output.
- Limits: HF_SENT_MAXLEN, HF_SENT_TRUNC; HF_GEN_MAXLEN, HF_GEN_MAX_NEW_MAX; HF_TX_MAXLEN; HF_TX_MIN_NEW (default 1).
- Model overrides: HF_EMBED_MODEL, HF_SUM_MODEL, HF_ZS_MODEL, HF_GEN_MODEL, HF_TX_MODEL.

### Translate security and offline policy

- hf_translate enforces safetensors-only and local-files-only loading.

  - No .bin weights are loaded. This avoids the torch.load vulnerability path; only safetensors are accepted.
  - local_files_only=true is used internally; with HF_HUB_OFFLINE=1, the server will never reach out to the network.
- If a required model isn't present locally in safetensors format, hf_translate returns a JSON envelope with error="unavailable" and a clear message. Other tools continue to function normally.
- /healthz includes a policies.translate block that reflects these enforcement rules, e.g. { local_files_only: true, safetensors_only: true }.
- Probe behavior: tools/hf_http_probe.mjs treats translate "unavailable" as SKIP to keep CI fast and green when the safetensors weights aren't on disk.

Recommended offline pre-cache (optional):

- When you have temporary internet access, pre-download a translation model that ships safetensors (for example, some Helsinki-NLP opus-mt variants or NLLB distilled models that provide safetensors). After the model is cached locally, keep HF_HUB_OFFLINE=1 for normal runs.
- Select the default model via HF_TX_MODEL (env). Control output length via HF_TX_MAXLEN. The server will still enforce safetensors-only even if a different model is chosen.

#### Cached Translate Models (defaults)

By default, hf_translate picks a safetensors-friendly model per language pair:

- en→es: Helsinki-NLP/opus-mt-tc-big-en-es
- en→fr: Helsinki-NLP/opus-mt-tc-big-en-fr
- fr→en: Helsinki-NLP/opus-mt-tc-big-fr-en
- en→de: facebook/wmt19-en-de
- de→en: facebook/wmt19-de-en

These are enforced as local-files-only and safetensors-only. If any aren’t present locally, translate returns error="unavailable" with a clear reason.

#### One-time caching workflow

Use the helper to cache translation models with safetensors:

- .venv/bin/python tools/cache_translate_models.py
  - Downloads tokenizer + safetensors weights for the default pairs above (or pass --pairs/--models).
  - Verifies presence offline and writes logs/hf_translate_cache_report.json.

- Keep HF_HUB_OFFLINE=1 for normal runs to guarantee local-only behavior.

Options:

- --pairs en-es en-fr fr-en en-de de-en
- --models REPO_ID ... (explicit list)
- Env HF_CACHE_PYTHON can point to a Python with huggingface_hub + transformers if your current venv lacks them.
- Runtime knob: HF_TX_MIN_NEW (default 1) ensures at least one token is generated; increase if you ever see overly-short outputs.

Probe: run `node tools/hf_http_probe.mjs` to validate endpoints with strict timeouts; it prints a PASS/FAIL summary.

- Push: pushes image to GHCR on main
- Deploy: optionally patches the weather-lsa Deployment in Kubernetes and waits for rollout
- If repository secrets are configured (GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_OAUTH, and IDs), the pipeline will run a non-mutating probe against the canary Ads API version and log the result. It is marked continue-on-error so PRs are not blocked by credential issues.
- Probe canary: python -m src --doctor-ads-canary
- Promote canary: python -m src --promote-canary
- Show effective/default/canary: python -m src --show-version

- make scheduler-safe (runs --scheduler with DRY_RUN + validate-only)

### Release

- Version is managed in pyproject.toml.
- Changelog is in CHANGELOG.md.
- Optional helper: make release creates a git tag for the current version and prints recent changelog headings.
- make install-dev – install dev/test deps
- make test – run unit tests
- make init / make doctor / make status – setup and checks
- make drain / make queue-status – worker operations
- make docker-build – build the slim container
- make docker-run – run the container with mapped health/metrics ports and mounted secrets/data

## Notifications

- Enable by setting ENABLE_NOTIFICATIONS=true in .env.
- SMS support has been removed. Only email is supported.

- Optional Prometheus metrics exporter. Set METRICS_PORT in .env (e.g., METRICS_PORT=9108).
- When enabled, the app exposes /metrics on the configured port with counters and latency histograms.

Example Prometheus scrape config (optional):

```yaml
static_configs:
  - targets: ['localhost:9108']
```

## Health endpoints

- Enable by setting HEALTH_PORT in .env (e.g., HEALTH_PORT=8081).
- Exposes lightweight HTTP endpoints:

  - /healthz – liveness (always returns 200 when the process is running)
  - /readyz – readiness with JSON payload:

  - ready (bool), db_ok (bool), creds_ok (bool)
  - breaker_open (bool), breaker_until (timestamp or null)
  - time (ISO timestamp)

## Docker deployment

- Build image:
  - make docker-build
- Run container (scheduler mode by default):
  - make docker-run
- The container exposes:
  - /healthz on HEALTH_PORT (default 8080 in Dockerfile)
- Mounts:
  - secrets/ (read-only) for client_secret.json and token.json
  - data/ for the SQLite DB (app.db)
- Healthcheck:
- Common tasks:

  - make venv / make install / make install-dev – set up environment
  - make dry-run – run in dry-run mode
  - make init – initialize project layout
  - make doctor – health check
  - make status – show campaign, hold, breaker, queue stats
  - make queue-status – show mutation queue
  - make drain – drain queued mutations (sequential)
  - METRICS_PORT=9108 make metrics – run with metrics exporter

## Audit trail

- Append-only audit logs capture who, what, when, why, old_value, new_value, request_id, and outcome.
- Stored in the database (audit_log table) and appended to daily JSONL files (logs/audit-YYYYMMDD.jsonl).
- CLI export:

  - Export last 7 days to JSONL (combined alerts, actions, notifications, audit):

  - python -m src --export-audit

  - Change window and format:

  - python -m src --export-audit --days 14 --format csv

## Notes

- With Basic developer tokens, live mutations may be blocked by Google Ads policies—use --dry-run and/or --validate-only to test safely.
- Keep only one venv (the .venv/ folder at the repo root).

### Region mappings and rules nuances

- Region mappings: set USE_REGION_MAPPINGS=false to ignore DB region-to-campaign mappings and use the default GOOGLE_ADS_CUSTOMER_ID/GOOGLE_ADS_CAMPAIGN_ID for actions. Default is enabled.
- Rules-driven PAUSE: when a rule explicitly requests PAUSE, the per-area cooldown suppression is bypassed so intentional rule actions are applied.

### Database

- SQLite is used by default (data/app.db).
- Optional: set DATABASE_URL=postgresql://user:pass@host:5432/db to use Postgres. Install psycopg2-binary in your environment to enable Postgres.

### Safety and resilience

- Local Services-only guard: enable via --lsa-only or REQUIRE_LOCAL_SERVICES_ONLY=true to prevent mutates on non-LOCAL_SERVICES campaigns.
- Backoff and circuit breaker for Ads API errors are configurable via .env.
- Idempotency and per-area cooldown reduce flapping; audit trail stored in SQLite.

## Structured error capture (optional)

- Sentry support is built-in. Configure SENTRY_DSN, SENTRY_ENV (e.g., prod, staging), and optional SENTRY_TRACES (0.0–1.0) in .env.
- Captures stack traces with tags and sanitized extras. Request headers/bodies are redacted (e.g., Authorization, developer-token, password, bearer tokens).
- Key capture points:
  - NWS fetch failures and unexpected errors (tag: component=nws).
  - Google Ads search/mutate errors (tag: component=ads, op=search|mutate).

## Developer shortcuts

- Common tasks live in the Makefile and VS Code tasks.
- Run tests with coverage HTML report: `make test-cov` (see `reports/htmlcov`).
- Full repo health check: `make health` (runs lint, type, tests, audits where available).
- Improvement backlog and audit: see `SUGGESTIONS.md`.
- Architecture overview: `docs/ARCHITECTURE.md`.
- CLI command errors for --status, --export-audit, --map-region, --unmap-region, --list-regions, --doctor-ads, --notify-test.

### OpenTelemetry tracing (optional)

- To export traces to OTLP (Tempo/Jaeger/other), set:

  - OTEL_EXPORTER_OTLP_ENDPOINT=<http://localhost:4318>
  - OTEL_SERVICE_NAME=weather-lsa-control
  - OTEL_TRACES_SAMPLER=parentbased_traceidratio
  - OTEL_TRACES_SAMPLER_ARG=0.1 (10% sampling)
- We instrument requests automatically and create spans around NWS fetches and Ads search/mutate calls.

## Troubleshooting

- Missing secrets/client_secret.json:

  - Place your OAuth client JSON under secrets/client_secret.json and re-run.
- Missing token.json:

  - Authenticate once by running python -m src to generate secrets/token.json.
- IDs must be digits (no dashes):

  - Set GOOGLE_ADS_CUSTOMER_ID, GOOGLE_ADS_CAMPAIGN_ID, and optionally GOOGLE_ADS_LOGIN_CUSTOMER_ID without dashes.

## Testing & Tools

[![tests](https://github.com/ORG/REPO/actions/workflows/tests.yml/badge.svg)](https://github.com/ORG/REPO/actions/workflows/tests.yml)

See docs/TESTING_HELPERS.md for:

- Playwright codegen (record flows)
- Pytest coverage+retry wrapper
- Memory summarize helper (JSONL)

### Discovery logs (Apify & Firecrawl)

- Apify: `npm run apify:discover -- "<keywords>" 3`

  - Artifacts: `logs/apify/<ts>_<slug>.json`, `logs/apify/discoveries.jsonl`
  - Includes a condensed input schema when available.
- Firecrawl: `npm run firecrawl:discover -- "<query>" 5`

  - Artifacts: `logs/firecrawl/<ts>_<slug>.json`, `logs/firecrawl/discoveries.jsonl`
  - Search-only; run the “Firecrawl: Crawl & Extract” task for full content.
Both commands print a one-line summary and exit 0 for tooling.
Both tasks forward prompt values via npm "--", e.g.: `npm run apify:discover -- "product page scraper" 3`.
