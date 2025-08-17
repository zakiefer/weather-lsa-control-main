# Runbook: Weather LSA Control

A concise, one-page operations guide for common actions.

## Kill switch (immediate stop of Ads mutations)
- Temporarily disable all enable/pause actions via the kill switch.
- Options:
  - CLI (one-off run): set env var when running the process
    - KILL_SWITCH=true python -m src --scheduler
  - .env (persistent for local runs): set KILL_SWITCH=true then restart the app
  - Kubernetes: set env var KILL_SWITCH=true on the Deployment and rollout
- Where it’s enforced: `weather_monitor.update_campaign_status()` and `worker` check `KILL_SWITCH` and skip mutates.
- Status check: `python -m src --status` shows breaker/queue; kill switch logs “KILL_SWITCH is active…” when a cycle runs.

## Clear storm holds
- Clears the last-alert time to immediately consider PAUSE/ENABLE actions (ignores the configured hold window).
- Command:
  - python -m src --clear-hold
- Effect: deletes `last_alert_utc` from `logs/storm_state.json`.
- Verify: `python -m src --status` prints Last alert (UTC): None and Storm hold: INACTIVE.

## Re-authenticate Google OAuth (token.json)
- When token is expired or missing, re-run the OAuth flow.
- Command:
  - python -m src
- This opens a browser window, then persists refreshed credentials to `secrets/token.json` and keychain.
- Headless: not supported for the interactive flow; ensure machine has a browser or use a remote port-forward.

## Rotate secrets
- Developer token (Google Ads):
  - Set SECRET_DEVELOPER_TOKEN as an environment variable (or update your OS keychain entry named developer_token).
  - Optionally update `.env` with GOOGLE_ADS_DEVELOPER_TOKEN for local dev (env vars still win).
  - Restart processes (scheduler/worker).
- SMTP password:
  - Set SECRET_SMTP_PASSWORD env var (or keychain entry `smtp_password`).
  - Ensure `.env` has SMTP_USER/SMTP_HOST/EMAIL_FROM/EMAIL_TO.
  - Validate with:
    - python -m src --send-test-email
- OAuth token:
  - Re-run `python -m src` to refresh; the app migrates `secrets/token.json` contents into the keychain automatically.

## Interpreting top error codes/messages
- 401/403 Google Ads (authorization):
  - Developer token may be test-only (DEVELOPER_TOKEN_NOT_APPROVED). Use TEST accounts or request Standard Access.
  - LOGIN_CUSTOMER_ID must be your manager account; CUSTOMER_ID must be digits only; OAuth token must be valid.
  - Try `python -m src --doctor-ads` and `--doctor-live` for diagnostics.
- 429 or 5xx from Ads API (rate limit/outage):
  - The client retries with exponential backoff and trips a circuit breaker after repeated failures.
  - When open, live mutates switch to validate-only behavior. Status shows “Ads circuit breaker: OPEN …”.
  - Wait for cooldown, or investigate credentials/network before re-enabling.
- Safety guard: “target campaign is not LOCAL_SERVICES”:
  - REQUIRE_LOCAL_SERVICES_ONLY=true blocks mutates on non-LSA campaigns. Either point to an LSA campaign or disable the guard for testing.
- SMTP errors (email not sent):
  - Check SMTP_HOST/PORT/USER/PASSWORD, EMAIL_FROM/EMAIL_TO. Server may require STARTTLS.
  - Validate with `python -m src --send-test-email`.
- DB locked / SQLite busy:
  - Usually transient. The worker uses a simple instance lock. Avoid multiple writers; consider Postgres (set DATABASE_URL) for more concurrency.

## Quick references
- Status overview (campaign, hold, breaker, queue, recent errors):
  - python -m src --status
- Queue operations:
  - python -m src --queue-status
  - python -m src --drain-queue
- Rules dry-run against recent CAPs:
  - python -m src --rules-dry-run --rules-file config/rules.yaml
- Audit export (last 7 days, JSONL):
  - python -m src --export-audit
- Scheduler with metrics and health:
  - METRICS_PORT=9108 HEALTH_PORT=8080 python -m src --scheduler
