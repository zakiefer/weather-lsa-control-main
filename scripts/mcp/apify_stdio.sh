#!/usr/bin/env bash
set -euo pipefail
# load .env.local
if [ -f .env.local ]; then
  while IFS='=' read -r k v; do
    [ -z "${k:-}" ] && continue
    [[ "$k" =~ ^# ]] && continue
    v="${v%$'\r'}"
    [ "$k" = "APIFY_TOKEN" ] && export APIFY_TOKEN="$v"
  done < .env.local
fi
: "${APIFY_TOKEN:?APIFY_TOKEN not set}"
# prefer local binary; else npx
if [ -x "./node_modules/.bin/apify-mcp" ]; then
  exec ./node_modules/.bin/apify-mcp
else
  exec npx -y apify-mcp
fi
