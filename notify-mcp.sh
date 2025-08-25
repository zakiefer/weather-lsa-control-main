#!/usr/bin/env bash
# Summarize MCP fleet status as a single line for VS Code post-start notification.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_DIR=".mcp/pids"
LOG_DIR=".mcp/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

running=0; total=0; names=()

# Prefer configured servers in .mcp/servers.conf
if [[ -f ".mcp/servers.conf" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// /}" || "${line:0:1}" == "#" ]] && continue
    name="${line%%|*}"; name="${name//[[:space:]]/}"
    [[ -z "$name" ]] && continue
    names+=("$name")
  done < .mcp/servers.conf
  total=${#names[@]}
  for name in "${names[@]}"; do
    pf="$PID_DIR/${name}.pid"; pid=""; alive=0
    [[ -f "$pf" ]] && pid="$(cat "$pf" 2>/dev/null || true)" || true
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      alive=1
    fi
    if [[ "$name" == "figma" ]]; then
      # HTTP health check fallback; try env, then infer from log
      p="${FIGMA_HTTP_PORT:-}"
      if [[ -z "$p" ]]; then
        log="$LOG_DIR/${name}.log"
        if [[ -f "$log" ]]; then
          p=$(grep -Eo "(:|port=)[0-9]{2,5}" "$log" | head -n1 | grep -Eo "[0-9]{2,5}" || true)
        fi
      fi
      [[ -z "$p" ]] && p=3855
      if curl -sS --max-time 0.3 "http://127.0.0.1:$p/mcp" >/dev/null 2>&1; then
        alive=1
      fi
    fi
    [[ $alive -eq 1 ]] && running=$((running+1))
  done
else
  # Fallback to PID files
  if compgen -G "$PID_DIR/*.pid" > /dev/null; then
    for pf in "$PID_DIR"/*.pid; do
      total=$((total+1))
      name="$(basename "$pf" .pid)"; names+=("$name")
      pid="$(cat "$pf" 2>/dev/null || true)"
      if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
        running=$((running+1))
      fi
    done
  fi
fi

if [[ $total -eq 0 ]]; then
  echo "MCP: No servers found. Run ./start-mcp.sh"
  exit 0
fi
icon="✅"; [[ $running -lt $total ]] && icon="⚠️"
printf "%s MCP: %d/%d running — %s\n" "$icon" "$running" "$total" "${names[*]}"
# Always exit 0 for tasks chaining
exit 0
