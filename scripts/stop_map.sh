#!/usr/bin/env bash
# Stop Streamlit Map servers listening on ports 8501-8503 and any residual Map.py process.
set -euo pipefail

cd "$(dirname "$0")/.."

# Kill by tracked PID if exists
if [ -f tmp/streamlit_map.pid ]; then
  PID=$(cat tmp/streamlit_map.pid || true)
  if [ -n "${PID:-}" ]; then
    kill "$PID" 2>/dev/null || true
  fi
  rm -f tmp/streamlit_map.pid
fi

# Kill any listeners on the common ports
kill_port() {
  local port="$1"
  # lsof exits non-zero when nothing is listening; ignore errors
  local pids
  pids=$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN -Fp 2>/dev/null | sed 's/^p//' || true)
  if [ -n "${pids:-}" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
  fi
}

for p in 8501 8502 8503; do
  kill_port "$p"
done

# Kill any remaining Streamlit Map runner
pkill -f 'streamlit run ui/pages/Map.py' 2>/dev/null || true

echo "Stopped Streamlit on :8501-:8503 (if running)"
