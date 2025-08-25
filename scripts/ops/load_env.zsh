#!/usr/bin/env zsh
# Load and export environment variables from .env.local for zsh shells.
# Usage: source ./scripts/ops/load_env.zsh
# Notes:
# - This will not echo secrets. It only exports them into your shell.
# - It applies a few compatibility mappings (HF token aliases, OpenRouter base URL).

set -euo pipefail

# Resolve repo root relative to this script
SCRIPT_DIR=${0:A:h}
REPO_ROOT=${SCRIPT_DIR:h:h}
ENV_FILE="$REPO_ROOT/.env.local"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Create it and add your keys. Aborting." >&2
  return 1
fi

# Export all assignments while sourcing
set -a
source "$ENV_FILE"
set +a

# Figma: minimal sanity log (no secret leak)
if [ -n "${FIGMA_CLIENT_ID:-}" ] && [ -n "${FIGMA_REDIRECT_URI:-}" ]; then
  echo "Figma env detected: FIGMA_CLIENT_ID=${FIGMA_CLIENT_ID}, FIGMA_REDIRECT_URI=${FIGMA_REDIRECT_URI}"
fi
if [ -n "${FIGMA_CLIENT_SECRET:-}" ]; then
  echo "Figma secret present (masked): $(printf '%s' "${FIGMA_CLIENT_SECRET}" | sed 's/./*/g;s/.\{4\}$/&/')"
fi

# Compatibility mappings for Hugging Face
if [[ -n "${HUGGINGFACEHUB_API_TOKEN:-}" && -z "${HF_TOKEN:-}" ]]; then
  export HF_TOKEN="$HUGGINGFACEHUB_API_TOKEN"
fi
if [[ -n "${HF_TOKEN:-}" && -z "${HUGGINGFACEHUB_API_TOKEN:-}" ]]; then
  export HUGGINGFACEHUB_API_TOKEN="$HF_TOKEN"
fi

# If using OpenRouter with OpenAI-compatible clients, ensure base URL is set
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  : ${OPENAI_BASE_URL:="https://openrouter.ai/api/v1"}
  export OPENAI_BASE_URL
fi

# Minimal sanity check (do not print secrets)
for v in HUGGINGFACEHUB_API_TOKEN HF_TOKEN OPENROUTER_API_KEY OPENAI_API_KEY FIRECRAWL_API_KEY OPENAI_BASE_URL; do
  if [[ -n "${(P)v:-}" ]]; then
    echo "$v=***loaded***"
  else
    echo "$v=***missing***"
  fi
done
