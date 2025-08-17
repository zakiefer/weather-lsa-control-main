# Logging & Errors – one‑page style guide

Keep logs clear, actionable, and safe. Treat logs as a shared interface for dev+ops.

## Principles
- Clarity over cleverness: short, specific, concrete.
- Actionability: say what failed, where, and what to do next.
- Context: include key IDs (last 4), states, thresholds, counters.
- No secrets/PII: never print tokens, passwords, or raw payloads.
- Economy: avoid spam; prefer one informative log over many trivial ones.

## Levels
- DEBUG: developer detail, noisy paths, one-time setup or branches.
- INFO: meaningful state changes, feature gates, decisions (enable/pause, breaker trip, dedupe).
- WARNING: degraded behavior with fallback used (breaker open, skipping mutate).
- ERROR: operation failed; system continues (API 5xx, validation errors).
- CRITICAL: unrecoverable; process should exit (avoid unless truly fatal).

## Style
- Tense/voice: present tense, neutral tone.
  - Good: "Circuit breaker OPEN until %s; skipping mutate."  
  - Avoid: blame, jokes, or ambiguous language.
- Structure: "what happened" + minimal context + next action/hint.
- Units & bounds: include units and limits ("cooldown 30 min", "qps=2, burst=5").
- IDs: prefer stable IDs; mask to last 4 (e.g., developer token, CID, campaign id). Hash when needed.

## Safety
- Scrub secrets: use `observability.sanitize` on headers/bodies; never log Authorization, refresh tokens, or full developer token.
- Cap payloads: slice large blobs (e.g., `resp.text[:500]`).
- External errors: log status, endpoint, and truncated body; avoid dumping structured PII.

## Exceptions
- Expected: log at INFO/WARNING with reason; avoid stack traces.
- Unexpected: log at ERROR with `exc_info=True` (or capture via Sentry) and minimal inputs (sanitized).
- Don’t double-log the same failure at multiple layers.

## Observability hooks
- Metrics: increment counters/histograms at edges (NWS fetch, Ads mutate, retries).
- Tracing: wrap external calls with `start_span` and annotate with op/component.
- Audit: use `record_audit_log` for user-visible changes (mutations, rollbacks).

## Patterns
- Rate limiting: log first occurrence at WARNING, subsequent at DEBUG; add counters.
- Retries: log attempt number and backoff; on final failure, log a single ERROR with summary.
- Circuit breaker: INFO when half-open/closed; WARNING when open; include until timestamp.

## Examples

Good (actionable, safe):
- `logging.warning("Circuit breaker OPEN for Ads until %s; skipping mutate.", until or "unknown")`
- `logging.error("Google Ads error %s for %s: %s", resp.status_code, url, resp.text[:500])`
- `capture_error(RuntimeError("ads_mutate_failed"), tags={"component": "ads", "op": "mutate"}, extras={"status": resp.status_code, "url": url})`

Avoid (unsafe/unclear):
- `logging.error(f"Token {headers['Authorization']}")`  # leaks secret
- `logging.info("It broke")`  # no context

## Checklist
- Is the message clear and actionable?
- Are secrets and large payloads sanitized/truncated?
- Right level for ops?
- Did we add metrics/trace/audit where relevant?
