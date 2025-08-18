#!/usr/bin/env bash
# Usage: capture_window.sh <AppName> <OutputPath>
# macOS: uses AppleScript + screencapture to capture a specific app window region.
# Linux fallback: uses ImageMagick (import) or grim if present.
set -euo pipefail

app_name="${1:-}"
outfile="${2:-}"
if [[ -z "$app_name" || -z "$outfile" ]]; then
  echo "Usage: $0 <AppName> <OutputPath>" >&2
  exit 2
fi
mkdir -p "$(dirname "$outfile")"

os="$(uname -s || echo Unknown)"

if [[ "$os" == "Darwin" ]]; then
  # Get position and size of the front window for the given app via AppleScript
  if ! command -v osascript >/dev/null 2>&1; then
    echo "[capture_window] osascript not available on macOS" >&2
    exit 1
  fi
  pos=$(osascript -e 'tell application "System Events" to tell process '"\"$app_name\""' to get position of front window' || true)
  size=$(osascript -e 'tell application "System Events" to tell process '"\"$app_name\""' to get size of front window' || true)
  if [[ -z "$pos" || -z "$size" ]]; then
    echo "[capture_window] Could not query window for app: $app_name. Ensure the app is running and Accessibility is enabled for Terminal/VS Code." >&2
    exit 1
  fi
  # Parse "x, y" and "w, h"
  IFS=',' read -r x y <<<"$pos"
  x=${x//[^0-9]/}
  y=${y//[^0-9]/}
  IFS=',' read -r w h <<<"$size"
  w=${w//[^0-9]/}
  h=${h//[^0-9]/}
  if ! command -v screencapture >/dev/null 2>&1; then
    echo "[capture_window] screencapture not found. On macOS, it should be present by default." >&2
    exit 1
  fi
  screencapture -x -R "${x},${y},${w},${h}" "$outfile"
  echo "[capture_window] Saved $outfile"
  exit 0
fi

# Linux fallbacks
if command -v grim >/dev/null 2>&1; then
  grim "$outfile"
  echo "[capture_window] Saved (grim) $outfile"
  exit 0
fi
if command -v import >/dev/null 2>&1; then
  import -window root "$outfile"
  echo "[capture_window] Saved (import root) $outfile"
  exit 0
fi

echo "[capture_window] No supported screenshot tool found (macOS screencapture, grim, or import)." >&2
exit 1
