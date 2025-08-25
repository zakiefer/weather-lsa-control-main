#!/usr/bin/env bash
set -euo pipefail
url="${1:-}"
if [ -z "${url}" ]; then echo "Usage: $0 <url>"; exit 2; fi
if [ -x "./node_modules/.bin/playwright" ]; then
  ./node_modules/.bin/playwright codegen "$url"
else
  npx -y playwright codegen "$url"
fi
