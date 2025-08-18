from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_dashboard_accessibility(page):
    """Basic a11y scan for Dashboard route using axe-core (placeholder)."""
    page.goto("http://localhost:8501/")

    # Inject axe; in CI, replace vendor/axe.min.js with the real asset
    axe_js = Path(__file__).parent / "vendor" / "axe.min.js"
    page.add_script_tag(path=str(axe_js))

    # Run axe in page context if real library is present
    result = page.evaluate(
        """
        () => {
          if (typeof axe === 'undefined' || typeof axe.run !== 'function') {
            return { error: 'axe-core not present', violations: [] };
          }
          return axe.run().then(r => ({ violations: r.violations }));
        }
        """
    )

    # Persist report for inspection
    out_dir = Path(__file__).resolve().parents[2] / "docs" / "a11y"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dashboard-axe.json").write_text(json.dumps(result, indent=2))

    # Soft assertion: if axe missing, mark xfail to avoid blocking until asset vendored
    if result.get("error"):
        pytest.xfail("axe-core vendor script missing; replace tests/e2e/vendor/axe.min.js with real build")

    # Fail on serious violations once axe is present
    violations = result.get("violations", [])
    severities = {v.get("impact") for v in violations}
    assert (
        "critical" not in severities and "serious" not in severities
    ), f"Accessibility issues found: {len(violations)} violations"
