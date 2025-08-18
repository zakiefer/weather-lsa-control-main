from __future__ import annotations

from pathlib import Path

import pytest

BASELINE_DIR = Path(__file__).parent / "baseline"
CURRENT_DIR = Path(__file__).parent / "current"


@pytest.mark.e2e
def test_home_visual_baseline(base_url: str, page):
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)

    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("text=Dashboard")

    baseline_path = BASELINE_DIR / "home.png"
    current_path = CURRENT_DIR / "home.png"

    page.screenshot(path=str(current_path), full_page=True)

    if not baseline_path.exists():
        # First run: establish baseline and xfail so CI doesn’t fail unexpectedly
        baseline_path.write_bytes(current_path.read_bytes())
        pytest.xfail("Baseline created for home.png; re-run to compare.")

    # Compare bytes as a simple smoke visual check (no diffing lib yet)
    assert baseline_path.read_bytes() == current_path.read_bytes(), "Visual regression detected for home.png"
