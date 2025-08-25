#!/usr/bin/env sh
# POSIX-compatible MCP stop script with stale PID cleanup and name-based fallback.
# - Cleans up stale PID files (.mcp/pids/*.pid) where process is not alive
# - Sends TERM, waits up to 5s, then KILLs lingering processes
# - Fallback: pkill -f by discovered process names from .mcp/servers.conf or .mcp.json/.vscode/mcp.json
# - Prints a clear summary and exits 0

set -eu
# pipefail portable-ish: avoid failing on pipelines by checking statuses explicitly where needed

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT_DIR"

PID_DIR=".mcp/pids"
LOG_DIR=".mcp/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

stopped=""
cleaned=""
missing=""

is_alive() {
  # $1: pid
  kill -0 "$1" 2>/dev/null
}

# 1) Stale PID cleanup
if ls "$PID_DIR"/*.pid >/dev/null 2>&1; then
  for pf in "$PID_DIR"/*.pid; do
    [ -f "$pf" ] || continue
    name=$(basename "$pf" .pid)
    pid=$(cat "$pf" 2>/dev/null || printf '')
    if [ -n "$pid" ] && is_alive "$pid"; then
      : # still running; will stop below
    else
      rm -f -- "$pf" 2>/dev/null || true
      cleaned="$cleaned $name"
    fi
  done
fi

# 2) Graceful stop (TERM -> wait -> KILL)
if ls "$PID_DIR"/*.pid >/dev/null 2>&1; then
  for pf in "$PID_DIR"/*.pid; do
    [ -f "$pf" ] || continue
    name=$(basename "$pf" .pid)
    pid=$(cat "$pf" 2>/dev/null || printf '')
    if [ -n "$pid" ] && is_alive "$pid"; then
      # Send TERM
      kill "$pid" 2>/dev/null || true
      # wait up to 5s
      end=$(( $(date +%s) + 5 ))
      while [ $(date +%s) -lt "$end" ]; do
        if ! is_alive "$pid"; then
          break
        fi
        sleep 1
      done
      # If still alive, KILL
      if is_alive "$pid"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      # Remove PID file regardless
      rm -f -- "$pf" 2>/dev/null || true
      stopped="$stopped $name:$pid"
    else
      missing="$missing $name"
      rm -f -- "$pf" 2>/dev/null || true
    fi
  done
fi

# 3) Name-based fallback via config discovery
# Collect candidate process match strings from .mcp/servers.conf and mcp JSON files
names=""
conf=".mcp/servers.conf"
if [ -f "$conf" ]; then
  # format: name|command
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    n=$(printf "%s" "$line" | awk -F '|' '{print $1}' | tr -d ' \t')
    cmd=$(printf "%s" "$line" | awk -F '|' '{print $2}')
    if [ -n "$n" ] && [ -n "$cmd" ]; then
      names="$names $n"
    fi
  done < "$conf"
fi
for jf in .mcp.json .vscode/mcp.json; do
  if [ -f "$jf" ]; then
    # extract command strings using python for portability
    cmds=$(python3 - "$jf" <<'PY'
import json,sys
p=sys.argv[1]
try:
  d=json.load(open(p,'r',encoding='utf-8'))
except Exception:
  d={}
servers=d.get('servers',{}) if isinstance(d,dict) else {}
for name,spec in servers.items():
  if isinstance(spec,dict):
    cmd=spec.get('command')
    if isinstance(cmd,str) and cmd.strip():
      print(name)
PY
)
    [ -n "$cmds" ] && names="$names $cmds"
  fi
done

# Attempt pkill -f for each name not already stopped/cleaned
for n in $names; do
  case " $stopped $cleaned " in
    *" $n "*) continue;;
  esac
  # pkill may not exist on some systems; guard it
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "$n" 2>/dev/null || true
  fi
done

# Final check: report any still-running processes that match our names
remaining=""
for n in $names; do
  if command -v pgrep >/dev/null 2>&1; then
    if pgrep -f "$n" >/dev/null 2>&1; then
      remaining="$remaining $n"
    fi
  fi
done

# 4) Summary (exit 0)
printf "--- MCP Stop Summary ---\n"
[ -n "$cleaned" ] && printf "Stale cleaned:%s\n" "$cleaned"
[ -n "$stopped" ] && printf "Stopped:%s\n" "$stopped"
[ -n "$missing" ] && printf "Missing (not running):%s\n" "$missing"
[ -n "$remaining" ] && printf "Possibly remaining:%s\n" "$remaining"
exit 0
