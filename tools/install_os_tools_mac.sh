#!/usr/bin/env bash
set -euo pipefail

echo "[install] Checking Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
  cat <<'EOT'
Homebrew is not installed. Install it first, then re-run this script:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
After install, ensure brew is on PATH (follow on-screen instructions), then rerun:
  brew update
EOT
  exit 1
fi

echo "[install] Updating Homebrew..."
brew update

echo "[install] Installing GitHub CLI (gh)..."
brew install gh || true

echo "[install] Installing kubectl..."
brew install kubectl || true

echo "[install] Installing sqlite3..."
brew install sqlite || true

echo "[install] Installing Docker Desktop (cask)..."
brew install --cask docker || true
echo "[note] Launch Docker.app once from Applications to finalize setup."

echo "[install] Installing Playwright browsers with project venv if available..."
if [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python -m playwright install || true
else
  python3 -m playwright install || true
fi

echo "[done] OS tools installation attempted."
