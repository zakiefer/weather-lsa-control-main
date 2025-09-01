#!/usr/bin/env bash
# One-click launcher for the Streamlit Map with port fallback and auto-open.
# - Prefers local .venv streamlit; installs if missing
# - Kills stray servers
# - Chooses first free port in 8501..8503
# - Opens default browser on macOS once ready
set -euo pipefail

# Move to repo root (this file lives in scripts/)
cd "$(dirname "$0")/.."

have() { command -v "$1" >/dev/null 2>&1; }

BIN=".venv/bin/streamlit"
if [ ! -x "$BIN" ]; then
  if have streamlit; then
    BIN="streamlit"
  else
    echo "streamlit not found; creating .venv and installing minimal deps..."
    if ! [ -x .venv/bin/python ]; then
      if have python3; then python3 -m venv .venv; else python -m venv .venv; fi
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python -m pip install -q --upgrade pip wheel
    if [ -f requirements.txt ] && grep -q '^streamlit==' requirements.txt; then
      python -m pip install -q -r requirements.txt
    else
      python -m pip install -q streamlit==1.36.0 streamlit-folium==0.18.0 folium==0.17.0
    fi
    BIN=".venv/bin/streamlit"
  fi
fi

# Kill any stray Streamlit processes for this Map page
pkill -f 'streamlit run ui/pages/Map.py' 2>/dev/null || true

# Choose first available port
PORT=""
for p in 8501 8502 8503; do
  if ! lsof -nP -iTCP:${p} -sTCP:LISTEN >/dev/null 2>&1; then PORT=${p}; break; fi
done
if [ -z "$PORT" ]; then
  echo "All ports 8501-8503 are busy. Stop existing servers first." >&2
  exit 1
fi

URL="http://localhost:${PORT}"
echo "Starting Streamlit Map on ${URL}"

# Start Streamlit server in background
"$BIN" run ui/pages/Map.py --server.port "${PORT}" --server.headless true &
PID=$!
mkdir -p tmp
echo "$PID" > tmp/streamlit_map.pid || true

# Wait for readiness then open browser
for i in {1..120}; do
  if curl -sfS "http://127.0.0.1:${PORT}" -o /dev/null; then
    echo "Running on ${URL}"
    case "$(uname -s)" in
      Darwin) open "${URL}" >/dev/null 2>&1 || true ;;
      *) : ;; # no-op for non-macOS
    esac
    break
  fi
  sleep 0.25
done

# Keep logs attached to this task
wait "$PID"
