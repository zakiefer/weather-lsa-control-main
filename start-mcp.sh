#!/usr/bin/env bash
# Start MCP-related background servers without blocking VS Code.
# - Idempotent: skips servers already running (checks PID files)
# - Non-blocking: uses nohup and short health checks; always exits 0
# - Safe on macOS: strips CRLF and removes quarantine attrs
#
# Usage:
#   ./start-mcp.sh [--timeout SECONDS] [--no-wait]
#
# Defaults: --timeout 2 seconds; waits for a quick health signal unless --no-wait

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# ------------ Env ------------
# Parse .env-like files: only KEY=VALUE lines, ignore others; do not source
parse_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    # trim leading/trailing spaces
    line="${line##+([[:space:]])}"
    line="${line%%+([[:space:]])}"
    # skip blanks, comments, or fenced code markers
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" == '```'* ]] && continue
    # must be KEY=VALUE pattern
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      key="${line%%=*}"
      val="${line#*=}"
      # strip optional surrounding quotes
      if [[ "$val" =~ ^".*"$ ]]; then val="${val:1:${#val}-2}"; fi
      if [[ "$val" =~ ^'.*'$ ]]; then val="${val:1:${#val}-2}"; fi
      export "$key"="$val"
    fi
  done < "$f"
}

parse_env_file ".env.mcp"
parse_env_file ".env.local"

# Default the model if not set
: "${OPENAI_MODEL:=}"
if [[ -z "${OPENAI_MODEL}" ]]; then
  export OPENAI_MODEL="gpt-5"
fi

# Enforce local-only Hugging Face/Transformers operation for any MCP servers that may use them.
# These can still be overridden by the environment or per-command prefixes in .mcp/servers.conf.
: "${HF_HOME:="${ROOT_DIR}/.cache/huggingface"}"
export HF_HOME
# Primary offline switches
export HF_HUB_OFFLINE="1"
export TRANSFORMERS_OFFLINE="1"
# Disable experimental transfer acceleration (may perform network checks)
export HF_HUB_ENABLE_HF_TRANSFER="0"
# If datasets are used anywhere, keep them offline as well (harmless otherwise)
export HF_DATASETS_OFFLINE="1"

# ------------ Args ------------
TIMEOUT=2
NO_WAIT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      shift
      TIMEOUT=${1:-2}
      ;;
    --no-wait)
      NO_WAIT=1
      ;;
    *)
      # ignore unknowns for forward-compat
      ;;
  esac
  [[ $# -gt 0 ]] && shift || true
done

# ------------ Safety helpers ------------
fix_crlf() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  # strip CRLF in-place (macOS-compatible sed)
  sed -i '' $'s/\r$//' "$f" 2>/dev/null || true
}

remove_quarantine() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  xattr -d com.apple.quarantine "$f" 2>/dev/null || true
}

# Ensure our own scripts are sane
fix_crlf "./start-mcp.sh"
fix_crlf "./stop-mcp.sh"
remove_quarantine "./start-mcp.sh"
remove_quarantine "./stop-mcp.sh"
chmod +x ./start-mcp.sh 2>/dev/null || true
chmod +x ./stop-mcp.sh 2>/dev/null || true

# ------------ Dirs ------------
PID_DIR=".mcp/pids"
LOG_DIR=".mcp/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# ------------ Discover servers ------------
# Preferred: read .mcp/servers.conf lines formatted as:
#   name|command
# Command runs via bash -lc in either the specified cwd from discovery or workspace root.
read_servers_conf() {
  local conf=".mcp/servers.conf"
  if [[ ! -f "$conf" ]]; then
    return 1
  fi
  # output as tab-separated: name command cwd
  while IFS= read -r line || [[ -n "$line" ]]; do
    # skip blanks and comments
    [[ -z "${line// /}" || "${line:0:1}" == "#" ]] && continue
    local name cmd
    name="${line%%|*}"
    cmd="${line#*|}"
    name="${name//[[:space:]]/}"
    if [[ -n "$name" && -n "$cmd" ]]; then
      printf "%s\t%s\t\n" "$name" "$cmd"
    fi
  done < "$conf"
  return 0
}

# Fallback: attempt to read .mcp.json and extract runnable entries
discover_servers() {
  python3 - "$ROOT_DIR" <<'PY'
import json, os, sys, shlex, re

root = sys.argv[1]
paths = [
  os.path.join(root, '.mcp.json'),
  os.path.join(root, '.vscode', 'mcp.json'),
]

def load(path):
  try:
    with open(path, 'r', encoding='utf-8') as f:
      return json.load(f)
  except Exception:
    return {}

cfgs = [load(p) for p in paths]

def subst_env_placeholders(s: str) -> str:
  def repl(m):
    key = m.group(1)
    return os.environ.get(key, '')
  return re.sub(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", repl, s)

skip = {'pytest', 'coverage', 'docker', 'kubernetes', 'ci', 'sql'}
items = {}

for cfg in cfgs:
  servers = (cfg.get('servers') or {}) if isinstance(cfg, dict) else {}
  for name, spec in servers.items():
    if name in skip:
      continue
    if not isinstance(spec, dict):
      continue
    cmd = spec.get('command')
    if not cmd or not isinstance(cmd, str):
      continue
    cmd = subst_env_placeholders(cmd)
    if not cmd.strip():
      continue
    cwd = spec.get('cwd') if isinstance(spec, dict) else None
    args = spec.get('args') if isinstance(spec, dict) else None
    if isinstance(args, list) and args:
      cmdline = ' '.join([shlex.quote(cmd)] + [shlex.quote(str(a)) for a in args])
    else:
      cmdline = cmd
    if cwd:
      cwd = cwd.replace('${workspaceFolder}', root)
    items[name] = (cmdline, cwd)

# Fallback: if nothing found, try common local servers
if not items:
  llm = os.path.join(root, 'mcp', 'llm-router', 'dist', 'index.js')
  if os.path.exists(llm):
    items['llm-router'] = ('node dist/index.js', os.path.join(root,'mcp','llm-router'))

for name, (cmd, cwd) in items.items():
  print('\t'.join([name, cmd, cwd or '']))
PY
}

# Build an array from discovery (bash 3.2 compatible; avoid mapfile)
SERVER_LINES=()
# Try servers.conf first
while IFS= read -r __line; do
  [[ -n "${__line}" ]] && SERVER_LINES+=("${__line}")
done < <(read_servers_conf || true)

# If empty, try discovery
if [[ ${#SERVER_LINES[@]} -eq 0 ]]; then
  while IFS= read -r __line; do
    [[ -n "${__line}" ]] && SERVER_LINES+=("${__line}")
  done < <(discover_servers || true)
fi

# If still empty, define a tiny local list as ultimate fallback (no-op)
if [[ ${#SERVER_LINES[@]} -eq 0 ]]; then
  fallback=$'llm-router\tnode dist/index.js\t'"${ROOT_DIR}/mcp/llm-router"
  SERVER_LINES=("$fallback")
fi

# ------------ Start loop ------------
printf "Starting MCP servers (timeout=%ss, wait=%s)\n" "$TIMEOUT" "$([ "$NO_WAIT" = 1 ] && echo "no" || echo "yes")"
echo "Effective OPENAI_MODEL=${OPENAI_MODEL}"

# Clean up stale PID files for servers that are no longer configured
configured_names=()
for line in "${SERVER_LINES[@]}"; do
  n="${line%%$'\t'*}"
  configured_names+=("$n")
done

# Build an awk-friendly regex of configured names
cfg_regex="^($(printf '%s|' "${configured_names[@]}" | sed 's/|$//'))$"
if compgen -G "$PID_DIR/*.pid" >/dev/null; then
  for pf in "$PID_DIR"/*.pid; do
    name="$(basename "$pf" .pid)"
    if ! printf "%s\n" "$name" | awk -v re="$cfg_regex" 'BEGIN{IGNORECASE=0} $0 ~ re {found=1} END{exit(found?0:1)}'; then
      rm -f "$pf" 2>/dev/null || true
    fi
  done
fi

summary=()
for line in "${SERVER_LINES[@]}"; do
  name="${line%%$'\t'*}"
  rest="${line#*$'\t'}"
  cmd="${rest%%$'\t'*}"
  cwd="${rest#*$'\t'}"
  [[ "$cwd" == "$cmd" ]] && cwd="" || true

  # Sanitize name for files
  sname="${name//[^A-Za-z0-9._-]/_}"
  pid_file="$PID_DIR/${sname}.pid"
  log_file="$LOG_DIR/${sname}.log"

  # Skip if running
  if [[ -f "$pid_file" ]]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
      printf "%-18s PID=%-7s status=already running\n" "$name" "$old_pid"
      summary+=("$name|$old_pid|already running")
      continue
    else
      rm -f "$pid_file" 2>/dev/null || true
    fi
  fi

  # Build launch command
  launch_cmd="$cmd"

  # Prefer project virtualenv Python if command starts with python/py
  if [[ -x ".venv/bin/python" ]]; then
    PY_EXE=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PY_EXE="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PY_EXE="$(command -v python)"
  else
    PY_EXE="python3"
  fi
  # Replace leading python executable if present
  first_word="${launch_cmd%% *}"
  rest_words="${launch_cmd#* }"
  if [[ "$first_word" == python* ]]; then
    if [[ "$launch_cmd" == *" "* ]]; then
      launch_cmd="$PY_EXE $rest_words"
    else
      launch_cmd="$PY_EXE"
    fi
  fi
  if [[ -n "$cwd" ]]; then
    launch_cmd="cd \"$cwd\" && $launch_cmd"
  fi

  # Start (truncate log first to avoid stale 'ready' lines matching health)
  : > "$log_file"
  # Use exec so the recorded PID is the long-lived child (no intermediate bash)
  # Detach from the caller session and ignore SIGHUP reliably
  nohup bash -lc "exec $launch_cmd" >> "$log_file" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$pid_file"

  # Health wait
  status="started"
  if [[ "$NO_WAIT" -ne 1 ]]; then
    end=$((SECONDS + TIMEOUT))
    healthy=0
    while [[ $SECONDS -lt $end ]]; do
      # Generic log-based readiness
      if grep -Ei -q "listening|ready|running|server started|connected|listens" "$log_file" 2>/dev/null; then
        healthy=1; break
      fi
      # Special-case: figma HTTP server — probe its /mcp endpoint
      if [[ "$name" == "figma" ]]; then
        # Try env-configured port first, then try to infer from log
        p="${FIGMA_HTTP_PORT:-}"
        if [[ -z "$p" ]]; then
          p=$(grep -Eo "(:|port=)[0-9]{2,5}" "$log_file" | head -n1 | grep -Eo "[0-9]{2,5}" || true)
        fi
        [[ -z "$p" ]] && p=3855
        if curl -sS --max-time 0.3 "http://127.0.0.1:$p/mcp" >/dev/null 2>&1; then
          healthy=1; break
        fi
      fi
      if ! kill -0 "$pid" 2>/dev/null; then
        healthy=0; break
      fi
      sleep 0.2
    done
    if [[ $healthy -eq 1 ]]; then
      status="healthy"
    else
      # Small grace retry window to catch late readiness after TIMEOUT
      if kill -0 "$pid" 2>/dev/null; then
        grace_end=$((SECONDS + 2))
        while [[ $SECONDS -lt $grace_end ]]; do
          if grep -Ei -q "listening|ready|running|server started|connected|listens" "$log_file" 2>/dev/null; then
            status="healthy"; healthy=1; break
          fi
          if [[ "$name" == "figma" ]]; then
            p="${FIGMA_HTTP_PORT:-}"
            if [[ -z "$p" ]]; then
              p=$(grep -Eo "(:|port=)[0-9]{2,5}" "$log_file" | head -n1 | grep -Eo "[0-9]{2,5}" || true)
            fi
            [[ -z "$p" ]] && p=3855
            if curl -sS --max-time 0.3 "http://127.0.0.1:$p/mcp" >/dev/null 2>&1; then
              status="healthy"; healthy=1; break
            fi
          fi
          if ! kill -0 "$pid" 2>/dev/null; then
            healthy=0; break
          fi
          sleep 0.2
        done
        if [[ $healthy -ne 1 ]]; then
          status="up"
        fi
      else
        status="failed"
      fi
    fi
  fi

  printf "%-18s PID=%-7s status=%s\n" "$name" "$pid" "$status"
  summary+=("$name|$pid|$status")
done

echo "--- Summary ---"
for s in "${summary[@]}"; do
  IFS='|' read -r n p st <<<"$s"
  printf "%-18s PID=%-7s status=%s\n" "$n" "$p" "$st"
done

echo
echo "Logs: $LOG_DIR/<name>.log"
echo "Model: ${OPENAI_MODEL}"
echo
cat <<'MSG'
How to verify:
  - Manually run start:   ./start-mcp.sh --timeout 2
  - Manually stop:        ./stop-mcp.sh
  - Check status:         ./status-mcp.sh
  - VS Code tasks:        Run task "MCP: Start all" or "MCP: Stop all" or "MCP: Status"
  - Logs live under:      .mcp/logs/

Tips:
  chmod +x ./start-mcp.sh ./stop-mcp.sh ./status-mcp.sh
  sed -i '' $'s/\r$//' ./start-mcp.sh ./stop-mcp.sh ./status-mcp.sh  # strip CRLF
  xattr -d com.apple.quarantine ./start-mcp.sh ./stop-mcp.sh ./status-mcp.sh || true
MSG

# Always exit 0 so VS Code autostart never blocks the window
exit 0
