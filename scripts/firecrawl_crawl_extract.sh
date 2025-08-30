#!/usr/bin/env bash
set -euo pipefail

SEED_URL="${1:-}"
MAX_DEPTH="${2:-2}"
INCLUDE="${3:-}"
EXCLUDE="${4:-}"

if [ -z "$SEED_URL" ]; then
  echo "Usage: firecrawl_crawl_extract.sh <seed_url> [max_depth] [include] [exclude]" >&2
  exit 1
fi

TMPDIR="logs/firecrawl"
mkdir -p "$TMPDIR"

CRAWL=$(jq -nc --arg url "$SEED_URL" --argjson depth "$MAX_DEPTH" \
   --arg inc "$INCLUDE" --arg exc "$EXCLUDE" \
   '{seed_url:$url,max_depth:$depth,include:[$inc],exclude:[$exc]}')

RESULT=$(echo "$CRAWL" | .venv/bin/python tools/mcp_verify_stdio.py --command scripts/mcp/firecrawl_stdio.sh --call firecrawl_crawl -)

CRAWL_ID=$(echo "$RESULT" | jq -r '.result.content[0].text' | jq -r '.crawl_id')
echo "Started crawl: $CRAWL_ID"

# poll until ready
for i in {1..30}; do
  STATUS=$(echo "{\"crawl_id\":\"$CRAWL_ID\"}" | .venv/bin/python tools/mcp_verify_stdio.py --command scripts/mcp/firecrawl_stdio.sh --call firecrawl_check_crawl_status -)
  DONE=$(echo "$STATUS" | jq -r '.result.content[0].text' | jq -r '.status')
  echo "Status: $DONE"
  [ "$DONE" = "completed" ] && break
  sleep 5
done

# extract
URLS=$(echo "$STATUS" | jq -r '.result.content[0].text' | jq -c '.urls')
EXTRACT=$(echo "{\"urls\":$URLS,\"extract\":{\"mode\":\"article\"}}" | .venv/bin/python tools/mcp_verify_stdio.py --command scripts/mcp/firecrawl_stdio.sh --call firecrawl_extract -)
OUTFILE="$TMPDIR/$(date +%s)_extract.json"
echo "$EXTRACT" > "$OUTFILE"
echo "Extract written to $OUTFILE"

# summarize top results (requires jq)
if command -v jq >/dev/null 2>&1; then
  SUMMARY=$(jq -r 'try .result.content[].text catch empty' "$OUTFILE" 2>/dev/null \
    | jq -r 'try (.web[]? | "- \(.title) — \(.url)") catch empty' 2>/dev/null \
    | head -n 5 || true)
  if [ -n "${SUMMARY:-}" ]; then
    echo "Summary of first results:"
    echo "$SUMMARY"
  else
    echo "No results to summarize."
  fi
else
  echo "jq not found; skipping summary. Install jq to enable summaries."
fi
