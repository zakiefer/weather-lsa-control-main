#!/usr/bin/env zsh
# Wrapper to load env for npm-run bash -lc 'source ./load_env.zsh; ...'
# Delegates to scripts/ops/load_env.zsh at repo root.
set -euo pipefail
SCRIPT_DIR=${0:A:h}
ROOT="$SCRIPT_DIR"
source "$ROOT/scripts/ops/load_env.zsh"
