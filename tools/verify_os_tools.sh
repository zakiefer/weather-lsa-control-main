#!/usr/bin/env bash
set -euo pipefail

ok() { printf "\033[32m✔\033[0m %s\n" "$1"; }
warn() { printf "\033[33m⚠\033[0m %s\n" "$1"; }
err() { printf "\033[31m✘\033[0m %s\n" "$1"; }

check_cmd() {
  local name="$1"; shift
  if command -v "$name" >/dev/null 2>&1; then ok "$name present"; else err "$name missing"; fi
}

check_cmd gh
check_cmd kubectl
check_cmd sqlite3
check_cmd docker

# Check playwright package presence
if command -v python3 >/dev/null 2>&1; then
  if python3 -c "import sys; import importlib.util as u; sys.exit(0 if u.find_spec('playwright') else 1)"; then
    ok "python playwright package installed"
  else
    warn "python playwright package not installed"
  fi
else
  warn "python3 not found"
fi

echo "Done."
