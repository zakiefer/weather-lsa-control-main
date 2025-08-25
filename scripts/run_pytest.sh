#!/usr/bin/env bash
set -euo pipefail
# Prefer local venv pytest; fallback to system or python -m pytest
run_pytest() {
  if [ -x .venv/bin/pytest ]; then
    .venv/bin/pytest "$@"
  elif command -v pytest >/dev/null 2>&1; then
    pytest "$@"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m pytest "$@"
  elif command -v python >/dev/null 2>&1; then
    python -m pytest "$@"
  else
    echo "pytest not found. Activate .venv or install dev deps." >&2
    return 127
  fi
}

OUTPUT_JUNIT="--junitxml=pytest-report.xml"

# Run tests with coverage + JUnit; on failure, retry only last-failed once.
run_pytest -q --maxfail=1 --disable-warnings \
  --cov=. --cov-report=term-missing:skip-covered \
  --cov-report=xml:coverage.xml \
  ${OUTPUT_JUNIT} \
  "$@" || FAILED=1
if [ "${FAILED:-0}" = "1" ]; then
  echo "─── pytest failed: attempting last-failed retry ───"
  if run_pytest -q --last-failed --last-failed-no-failures all ${OUTPUT_JUNIT} "$@"; then
    echo "Retry succeeded."
    exit 0
  else
    echo "Retry failed. Showing concise failure summary:"
    run_pytest -q --last-failed --maxfail=1 -rA || true
    exit 1
  fi
fi

# Also generate HTML coverage report directory (htmlcov/) for CI artifact/browsing
if [ -x .venv/bin/python ]; then
  .venv/bin/python -m coverage html -d htmlcov || .venv/bin/coverage html -d htmlcov || true
else
  if command -v coverage >/dev/null 2>&1; then
    coverage html -d htmlcov || true
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m coverage html -d htmlcov || true
  elif command -v python >/dev/null 2>&1; then
    python -m coverage html -d htmlcov || true
  fi
fi
