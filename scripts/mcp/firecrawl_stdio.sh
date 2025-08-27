#!/usr/bin/env bash
set -euo pipefail
# Load .env.local KEY=VALUE lines safely (no eval of quotes/exports)
ENV_FILE=".env.local"
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    [ -z "${k:-}" ] && continue
    [[ "$k" =~ ^# ]] && continue
    # trim CR
    v="${v%$'\r'}"
    if [ "$k" = "FIRECRAWL_API_KEY" ]; then export FIRECRAWL_API_KEY="$v"; fi
    if [ "$k" = "FIRECRAWL_LOCAL_ONLY" ]; then export FIRECRAWL_LOCAL_ONLY="$v"; fi
    if [ "$k" = "FIRECRAWL_DISABLE_CLOUD" ]; then export FIRECRAWL_DISABLE_CLOUD="$v"; fi
  done < "$ENV_FILE"
fi
# In local-only mode, skip requiring an API key
if [ "${FIRECRAWL_LOCAL_ONLY:-}" = "1" ] || [ "${FIRECRAWL_DISABLE_CLOUD:-}" = "1" ]; then
  : "Local mode enabled: skipping FIRECRAWL_API_KEY check"
else
  # Fail early if key missing
  : "${FIRECRAWL_API_KEY:?FIRECRAWL_API_KEY not set}"
fi
# Prefer local binary; fallback to npx
if [ -x "./node_modules/.bin/firecrawl-mcp" ]; then
  exec ./node_modules/.bin/firecrawl-mcp
else
  exec npx -y firecrawl-mcp
fi
