#!/usr/bin/env bash
# Show MCP background servers status by reading .mcp/pids and verifying processes.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_DIR=".mcp/pids"
LOG_DIR=".mcp/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# Load env to surface effective model or other settings, without sourcing arbitrary code
parse_env_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line##+([[:space:]])}"
    line="${line%%+([[:space:]])}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" == '```'* ]] && continue
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      key="${line%%=*}"; val="${line#*=}"
      if [[ "$val" =~ ^".*"$ ]]; then val="${val:1:${#val}-2}"; fi
      if [[ "$val" =~ ^'.*'$ ]]; then val="${val:1:${#val}-2}"; fi
      export "$key"="$val"
    fi
  done < "$f"
}
parse_env_file ".env.mcp"
parse_env_file ".env.local"
: "${OPENAI_MODEL:=}"
[[ -z "${OPENAI_MODEL}" ]] && OPENAI_MODEL="gpt-5"

sed -i '' $'s/\r$//' ./status-mcp.sh 2>/dev/null || true
xattr -d com.apple.quarantine ./status-mcp.sh 2>/dev/null || true
chmod +x ./status-mcp.sh 2>/dev/null || true

# Build configured server list from servers.conf (name|command)
SERVER_NAMES=()
if [[ "${SKIP_SERVERS_CONF:-0}" != "1" ]]; then
  if [[ -f ".mcp/servers.conf" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "${line// /}" || "${line:0:1}" == "#" ]] && continue
      name="${line%%|*}"; name="${name//[[:space:]]/}"
      [[ -n "$name" ]] && SERVER_NAMES+=("$name")
    done < .mcp/servers.conf
  fi
fi

# Also include servers from VS Code JSON if explicitly enabled (opt-in)
add_from_json() {
  local json_path="$1"
  [[ -f "$json_path" ]] || return 0
  while IFS= read -r line; do
    # Lines may be either server names or WARN: messages
    if [[ "$line" == WARN:* ]]; then
      # Print user-friendly warning; strip the prefix
      echo "${line#WARN: }"
    elif [[ -n "$line" ]]; then
      SERVER_NAMES+=("$line")
    fi
  done < <(python3 - "$json_path" <<'PY'
import json, re, sys
p = sys.argv[1]
try:
  with open(p, 'r', encoding='utf-8') as f:
    s = f.read()
  # JSONC compatibility: remove line comments that start a line, and block comments
  s = re.sub(r'(?m)^[ \t]*//.*$', '', s)
  s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
  # Remove trailing commas before } or ]
  s = re.sub(r',\s*([}\]])', r'\1', s)
  cfg = json.loads(s)
  servers = (cfg.get('servers') or {})
  for n, obj in servers.items():
    if not isinstance(n, str) or not n.strip():
      continue
    # Special-case: ignore invalid figma HTTP entry using env port placeholder
    if n == 'figma' and isinstance(obj, dict):
      url = obj.get('url')
      if isinstance(url, str) and '${env:FIGMA_HTTP_PORT}' in url:
        print('WARN: ⚠️ Ignored invalid Figma MCP entry using ${env:FIGMA_HTTP_PORT}')
        continue
    print(n)
except Exception:
  pass
PY
  )
}

if [[ "${INCLUDE_VSCODE_MCP_JSON:-0}" = "1" ]]; then
  json_path="${VSCODE_MCP_JSON_PATH:-.vscode/mcp.json}"
  add_from_json "$json_path"
fi

# Deduplicate while preserving order (Bash 3.2 compatible)
if [[ ${#SERVER_NAMES[@]} -gt 0 ]]; then
  _dedup=()
  _seen_list=""
  for _n in "${SERVER_NAMES[@]}"; do
    case " $_seen_list " in
      *" $_n "*) : ;; # already seen
      *) _dedup+=("$_n"); _seen_list="$_seen_list $_n" ;;
    esac
  done
  SERVER_NAMES=("${_dedup[@]}")
fi

printf "%-18s %-8s %-8s %-6s %s\n" "NAME" "PID" "ALIVE" "PORT" "LOG"
printed_any=0
if [[ ${#SERVER_NAMES[@]} -gt 0 ]]; then
  for name in "${SERVER_NAMES[@]}"; do
    pf="$PID_DIR/${name}.pid"
    pid=""; alive="no"; log="$LOG_DIR/${name}.log"; port=""
    if [[ -f "$pf" ]]; then
      pid="$(cat "$pf" 2>/dev/null || true)"
      if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
        alive="yes"
      fi
    fi
    if [[ -z "$port" && -f "$log" ]]; then
      port=$(grep -Eo "(:|port=)[0-9]{2,5}" "$log" | head -n1 | grep -Eo "[0-9]{2,5}" || true)
    fi
    if [[ "$name" == "figma" ]]; then
      p="${port:-${FIGMA_HTTP_PORT:-3855}}"
      if [[ -n "$p" ]]; then
        if curl -sS --max-time 0.3 "http://127.0.0.1:$p/mcp" >/dev/null 2>&1; then
          alive="yes"; port="$p"
        fi
      fi
    fi
    printf "%-18s %-8s %-8s %-6s %s\n" "$name" "${pid:-}" "$alive" "${port:-}" "$log"
    printed_any=1
  done
else
  if compgen -G "$PID_DIR/*.pid" > /dev/null; then
    for pf in "$PID_DIR"/*.pid; do
      name="$(basename "$pf" .pid)"
      pid="$(cat "$pf" 2>/dev/null || true)"
      alive="no"; log="$LOG_DIR/${name}.log"; port=""
      [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null && alive="yes" || true
      if [[ -f "$log" ]]; then
        port=$(grep -Eo "(:|port=)[0-9]{2,5}" "$log" | head -n1 | grep -Eo "[0-9]{2,5}" || true)
      fi
      printf "%-18s %-8s %-8s %-6s %s\n" "$name" "${pid:-}" "$alive" "${port:-}" "$log"
      printed_any=1
    done
  fi
fi

if [[ "$printed_any" = 0 ]]; then
  echo "No configured servers or PID files found."
fi

echo "Effective OPENAI_MODEL=${OPENAI_MODEL}"

exit 0
