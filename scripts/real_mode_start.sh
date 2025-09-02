#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Pick a python
if [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
else
  if command -v python3 >/dev/null 2>&1; then PY="python3"; elif command -v python >/dev/null 2>&1; then PY="python"; else echo "No python found" >&2; exit 127; fi
fi

# Ensure streamlit present
if ! "$PY" - <<'PY'
import importlib
import sys
sys.exit(0 if importlib.util.find_spec("streamlit") else 1)
PY
then
  "$PY" -m pip install -q --upgrade pip wheel
  # Try repo requirements first if pins exist, else minimal set
  if [ -f requirements.txt ] && grep -q '^streamlit==' requirements.txt; then
    "$PY" -m pip install -q -r requirements.txt
  else
    "$PY" -m pip install -q streamlit==1.36.0 streamlit-folium==0.18.0 folium==0.17.0
  fi
fi

PORT=${PORT:-8520}
unset E2E_AUTH_BYPASS E2E_SPC_FIXTURE E2E_FORCE_SVG || true
echo "Starting Streamlit (real-mode) on :$PORT"
# Enable server-side auto-auth for E2E so the Map renders without manual login,
# while keeping all data sources real (no fixtures toggled here)
E2E_AUTO_AUTH=1 exec "$PY" -m streamlit run ui/pages/Map.py --server.port "$PORT" --server.headless true
