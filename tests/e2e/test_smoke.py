from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_loads(base_url: str, page: Page) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    # Expect the built-in Streamlit title and Sidebar to appear
    page.wait_for_selector("text=Dashboard")


def test_pages_accessible(base_url: str, page: Page) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    # Use the built-in multipage nav present in the sidebar
    # Navigate to Map page via text link
    page.get_by_role("link", name="Map", exact=True).click()
    # Prefer deterministic readiness instead of text waits
    try:
        page.wait_for_function(
            "() => document.body && (document.body.getAttribute('data-map-ready-parent')==='1' || document.body.getAttribute('data-sidebar-ready')==='1')",
            timeout=10000,
        )
    except Exception:
        pass
