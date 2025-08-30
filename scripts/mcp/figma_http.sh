#!/usr/bin/env bash
set -euo pipefail

# Load env (ignore if missing)
[ -f .env.local ] && set -a && . ./.env.local && set +a || true

: "${FIGMA_HTTP_PORT:=3845}"

# Run the local HTTP server implementation
exec node mcp/figma-server-http.js
