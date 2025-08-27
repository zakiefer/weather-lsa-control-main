#!/usr/bin/env bash
set -euo pipefail

PROBE_LOG=/tmp/hf_local_probe.log
MCP_LOG=.mcp/logs/huggingface.log
URL=${URL:-http://127.0.0.1:3865}
TIMEOUT=${TIMEOUT:-5000}

# 1) Run local probe
node tools/hf_http_probe.mjs --url "$URL" --timeout "$TIMEOUT" > "$PROBE_LOG" 2>&1 || true

# 2) Check health and tools
PASS_OK=$(grep -q '"status":"PASS"' "$PROBE_LOG" && echo yes || echo no)
TOOLS_OK=yes
for t in hf_sentiment hf_embeddings hf_summarize hf_zero_shot hf_generate hf_translate; do
  if ! grep -q "$t" "$PROBE_LOG"; then
    TOOLS_OK=no
    break
  fi
done

# 3) Confirm no remote calls
LEAK_OK=yes
if grep -qi "hf.co/mcp" "$PROBE_LOG" 2>/dev/null; then
  LEAK_OK=no
fi
if [ -f "$MCP_LOG" ] && grep -qi "hf.co/mcp" "$MCP_LOG" 2>/dev/null; then
  LEAK_OK=no
fi

ALL_OK=no
if [ "$PASS_OK" = yes ] && [ "$TOOLS_OK" = yes ] && [ "$LEAK_OK" = yes ]; then
  ALL_OK=yes
fi

# 4) Single-line summary
if [ "$ALL_OK" = yes ]; then
  echo "✅ Hugging Face MCP is local-only and healthy"
  exit 0
else
  echo "❌ Hugging Face MCP leak detected — see $PROBE_LOG"
  exit 1
fi
