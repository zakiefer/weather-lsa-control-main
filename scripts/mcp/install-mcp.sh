#!/usr/bin/env sh
# Installs MCP server CLIs globally if missing (idempotent).
# Uses npm -g for installation. Safe on re-run.
# Logs minimal progress and continues on individual failures.

set -euo pipefail

need() {
  # $1: command name
  command -v "$1" >/dev/null 2>&1
}

echo "== MCP installer =="
if ! need npm; then
  echo "ERROR: npm not found. Please install Node.js/npm (e.g., via nvm or brew)." >&2
  exit 1
fi

install_npm() {
  # $1: package name
  # $2: expected bin name (defaults to package)
  pkg="$1"
  bin="${2:-$1}"
  if need "$bin"; then
    echo "✓ $bin already installed"
    return 0
  fi
  echo "→ Installing $pkg (bin: $bin)"
  if npm list -g --depth=0 "$pkg" >/dev/null 2>&1; then
    # Package present but bin not in PATH (rare); try force reinstall to restore bins
    if ! npm install -g "$pkg" >/dev/null 2>&1; then
      echo "WARN: npm reinstall failed for $pkg; will verify PATH for $bin" >&2
    fi
  else
    if ! npm install -g "$pkg"; then
      echo "WARN: npm install failed for $pkg; will try simple verify for $bin" >&2
    fi
  fi
  if need "$bin"; then
    echo "✓ $bin installed"
    return 0
  fi
  echo "WARN: $bin still not found after install. Check npm logs or adjust package name." >&2
  return 0
}

# Package list (pkg => bin). Adjust bin if different from package name.
install_npm figma-mcp-http figma-mcp-http
install_npm github-mcp github-mcp
install_npm playwright-mcp playwright-mcp
install_npm sentry-mcp sentry-mcp
install_npm huggingface-mcp huggingface-mcp
install_npm deepwiki-mcp deepwiki-mcp
install_npm markitdown-mcp markitdown-mcp
install_npm microsoft-docs-mcp microsoft-docs-mcp
install_npm context7-mcp context7-mcp
install_npm imagesorcery-mcp imagesorcery-mcp
install_npm codacy-mcp codacy-mcp
install_npm sequentialthinking-mcp sequentialthinking-mcp
install_npm memory-mcp memory-mcp
install_npm apify-mcp apify-mcp
install_npm clarity-mcp clarity-mcp
install_npm firecrawl-mcp firecrawl-mcp

echo "== MCP installer complete =="
