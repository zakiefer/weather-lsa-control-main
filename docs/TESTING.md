# Testing and E2E

This project includes a unit test suite (pytest) and an optional Playwright-based E2E harness for the Streamlit UI.

## Prereqs

- Python 3.9+
- Project virtualenv `.venv` with dev deps installed
- One-time Playwright browser install

## Setup

1) Install dev dependencies

   - In this repo's venv: `pip install -r requirements-dev.txt`
   - Then install browsers once: `python -m playwright install --with-deps`

2) Create a local `.env` (ignored by Git) if you want custom admin creds:

   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=Walmart2025!

The UI harness automatically seeds this admin if absent.

## Running the app locally

- VS Code task: "run:streamlit:bg" starts Streamlit on a free port.
- Or use: `.venv/bin/python tools/run_ui.py`

## E2E tests (eyes-on)

- The E2E test files are under `tests/e2e/`.
- VS Code tasks:
   - "e2e:pytest" runs E2E headless
   - "e2e:headful" runs E2E with a visible browser (E2E_HEADFUL=1)
- To run only E2E in a terminal: `pytest -q tests/e2e`
- To see the browser: set `E2E_HEADFUL=1` in your environment.
- To keep the app running externally and reuse it, set `E2E_BASE_URL=http://localhost:8510`.

Selectors are stabilized using `ui/testids.py`. Test IDs render only when `E2E_TEST_IDS=1`.

Artifacts:
- Screenshots (when added) go to `docs/img/e2e/`
- Accessibility reports go to `docs/a11y/`

## Accessibility checks (axe)

An initial test lives at `tests/e2e/test_accessibility.py`. Replace the placeholder `tests/e2e/vendor/axe.min.js` with the official axe build to enable real checks.

## Notes

- The E2E fixtures boot Streamlit via `tools/run_ui.py` if `E2E_BASE_URL` is not provided.
- The seeding logic ensures the admin user exists. Sign-in flows can be added later using the built-in auth UI.
