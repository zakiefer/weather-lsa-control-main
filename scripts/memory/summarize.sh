#!/usr/bin/env bash
set -euo pipefail
LOG="${1:-memory_log.jsonl}"
if [ ! -f "$LOG" ]; then
  echo "No memory log found at $LOG"
  exit 0
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found; showing raw tail:"
  tail -n 50 "$LOG"
  exit 0
fi
jq -r '
  try (map(.goal + ": " + (.result // .summary // "n/a")) | .[])
  catch "Invalid JSONL; showing last 50 lines"
' <(jq -s . "$LOG") || tail -n 50 "$LOG"
