# Testing Helpers

This repo includes three developer helpers to speed up UI recording and testing.

- Playwright codegen: interactively record UI flows from a given URL
- Pytest wrapper: run tests with coverage and a one-time retry for flaky failures
- Memory summarize: pretty-print the latest entries in a JSONL memory log

## Playwright codegen

Record UI interactions and generate Playwright scripts.

- VS Code task: "Tests: Generate UI via Playwright"
  - Prompts for GEN_URL (default: http://localhost:3000). You can change this each run.
- Direct script:
  - bash scripts/tests/playwright_codegen.sh http://localhost:3000

Expected behavior
- Opens a Playwright recorder window pointed at GEN_URL.
- If browsers are not installed, you'll see a message suggesting: `npx playwright install`.
- If the URL is not reachable, you'll see `net::ERR_CONNECTION_REFUSED`.

Troubleshooting
- Install browsers: npx playwright install (or install only Chromium with `npx playwright install chromium`).
- Use a reachable URL (start your app or point to a live environment).
- Ensure Node is available (node --version) when using npx.

## Pytest wrapper (coverage + retry)

Run tests with coverage and retry last-failed once if the first run fails.

- VS Code: you can wire to a task or use the provided script directly.
- Direct script:
  - bash scripts/run_pytest.sh
  - Pass custom args as needed (e.g., `bash scripts/run_pytest.sh tests/e2e`).

Expected behavior
- Produces terminal coverage summary and writes coverage.xml at repo root.
- On failure, prints a message and retries only the last failed tests once.
- If the retry fails, prints a concise failure summary and exits non-zero.
- The script prefers `.venv/bin/pytest`; falls back to system `pytest` or `python -m pytest`.

CI/CD note
- This wrapper is used in CI to keep logs short while still generating coverage.xml.

## Memory summarize (JSONL)

Summarize JSONL memory logs to quickly inspect recent entries.

- VS Code task: "Memory: Summarize"
- Direct script:
  - bash scripts/memory/summarize.sh
  - Or provide a path: `bash scripts/memory/summarize.sh logs/memory.jsonl`

Expected behavior
- If `jq` is available and the file is valid JSONL, prints lines like:
  - `<goal text>: <result | summary>`
- If `jq` is not available or JSON is invalid, falls back to `tail -n 50` of the file.
- If the file does not exist, prints `No memory log found at <path>` and exits 0.

## VS Code inputs

- GEN_URL: the Playwright codegen task prompts for this input each run; change it as needed.

## Appendix: Task labels

- Tests: Generate UI via Playwright
- Memory: Summarize

See also: .vscode/tasks.json for the full list of available tasks.
