#!/usr/bin/env bash
# Stop MCP background servers using PID files in .mcp/pids.
# Sends SIGTERM, waits briefly, then SIGKILL if still alive.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_DIR=".mcp/pids"
LOG_DIR=".mcp/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# Safety: normalize line endings and remove quarantine on both scripts
sed -i '' $'s/\r$//' ./stop-mcp.sh 2>/dev/null || true
sed -i '' $'s/\r$//' ./start-mcp.sh 2>/dev/null || true
xattr -d com.apple.quarantine ./stop-mcp.sh 2>/dev/null || true
xattr -d com.apple.quarantine ./start-mcp.sh 2>/dev/null || true
chmod +x ./stop-mcp.sh ./start-mcp.sh 2>/dev/null || true

stopped=()
if compgen -G "$PID_DIR/*.pid" > /dev/null; then
  for pf in "$PID_DIR"/*.pid; do
    [[ -f "$pf" ]] || continue
    name="$(basename "$pf" .pid)"
    pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      printf "Stopping %-18s PID=%s..." "$name" "$pid"
      kill "$pid" 2>/dev/null || true
      # wait up to 3s
      end=$((SECONDS + 3))
      while [[ $SECONDS -lt $end ]]; do
        if ! kill -0 "$pid" 2>/dev/null; then
          break
        fi
        sleep 0.2
      done
      if kill -0 "$pid" 2>/dev/null; then
        printf " still alive; sending SIGKILL..."
        kill -9 "$pid" 2>/dev/null || true
      fi
      echo " done"
    else
      echo "${name}: not running"
    fi
    rm -f "$pf" 2>/dev/null || true
    stopped+=("$name|${pid:-}-")
  done
else
  echo "No PID files found in $PID_DIR"
fi

echo "--- Stopped summary ---"
for s in "${stopped[@]}"; do
  IFS='|' read -r n p <<<"$s"
  printf "%-18s PID=%s\n" "$n" "$p"
 done

exit 0
