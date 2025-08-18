#!/usr/bin/env bash
# Usage: capture_screen.sh <OutputPath>
# macOS: screencapture; Linux: grim or import
set -euo pipefail

outfile="${1:-}"
if [[ -z "$outfile" ]]; then
  echo "Usage: $0 <OutputPath>" >&2
  exit 2
fi
mkdir -p "$(dirname "$outfile")"

os="$(uname -s || echo Unknown)"
if [[ "$os" == "Darwin" ]]; then
  if ! command -v screencapture >/dev/null 2>&1; then
    echo "[capture_screen] screencapture not found" >&2
    exit 1
  fi
  screencapture -x "$outfile"
  echo "[capture_screen] Saved $outfile"
  exit 0
fi

if command -v grim >/dev/null 2>&1; then
  grim "$outfile"
  echo "[capture_screen] Saved (grim) $outfile"
  exit 0
fi
if command -v import >/dev/null 2>&1; then
  import -window root "$outfile"
  echo "[capture_screen] Saved (import root) $outfile"
  exit 0
fi

echo "[capture_screen] No supported screenshot tool found." >&2
exit 1
