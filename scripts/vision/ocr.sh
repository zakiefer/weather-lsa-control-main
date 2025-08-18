#!/usr/bin/env bash
# Usage: ocr.sh <ImagePath>
# Uses tesseract if available; otherwise tries macOS 'safaridriver --diagnose' as a placeholder (no-op) and fails cleanly.
set -euo pipefail

img="${1:-}"
if [[ -z "$img" ]]; then
  echo "Usage: $0 <ImagePath>" >&2
  exit 2
fi

if command -v tesseract >/dev/null 2>&1; then
  # Output to stdout
  tesseract "$img" stdout 2>/dev/null || {
    echo "[ocr] tesseract failed on $img" >&2
    exit 1
  }
  exit 0
fi

echo "[ocr] tesseract not found. Install with: brew install tesseract (macOS) or apt-get install tesseract-ocr (Debian/Ubuntu)." >&2
exit 1
