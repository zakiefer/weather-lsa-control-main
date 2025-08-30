#!/usr/bin/env bash
set -euo pipefail

# Sentry REST probe: prints org and projects JSON previews.
# Does NOT source the entire .env.local to avoid side effects; parses only needed keys.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env.local"

parse_env_key() {
  local key="$1"
  if [ -n "${!key-}" ]; then
    printf '%s' "${!key}"
    return 0
  fi
  if [ -f "$ENV_FILE" ]; then
    local line
    line=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null || true)
    if [ -n "$line" ]; then
      local val
      val="${line#*=}"
      # strip surrounding quotes if present
      if [[ "$val" =~ ^\".*\"$ ]] || [[ "$val" =~ ^\'.*\'$ ]]; then
        val="${val:1:${#val}-2}"
      fi
      printf '%s' "$val"
      return 0
    fi
  fi
  return 1
}

SENTRY_AUTH_TOKEN="$(parse_env_key SENTRY_AUTH_TOKEN || true)"
SENTRY_ORG_SLUG="$(parse_env_key SENTRY_ORG_SLUG || true)"
SENTRY_ORG_SLUG="${SENTRY_ORG_SLUG:-zakiefer}"

if [ -z "${SENTRY_AUTH_TOKEN:-}" ]; then
  echo "ERR: SENTRY_AUTH_TOKEN not set (env or .env.local)" >&2
  exit 2
fi

echo "=== Sentry REST Token Check ==="
echo "LEN:${#SENTRY_AUTH_TOKEN}"
echo "START:${SENTRY_AUTH_TOKEN:0:6}"
echo "END:${SENTRY_AUTH_TOKEN: -6}"

BASE="https://sentry.io/api/0"
HDR_AUTH=( -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" )
HDR_JSON=( -H "Accept: application/json" -H "Content-Type: application/json" )

echo "=== GET org ==="
curl -sS -w "\nHTTP_STATUS:%{http_code}\n" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  "$BASE/organizations/$SENTRY_ORG_SLUG/" | sed -e 's/\r$//' | head -c 600; echo

echo "=== GET projects ==="
curl -sS -w "\nHTTP_STATUS:%{http_code}\n" "${HDR_AUTH[@]}" "${HDR_JSON[@]}" \
  "$BASE/organizations/$SENTRY_ORG_SLUG/projects/" | sed -e 's/\r$//' | head -c 600; echo
