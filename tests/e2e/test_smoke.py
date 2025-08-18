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
    page.get_by_role("link", name="Map").click()
    page.wait_for_selector("text=Basemap")
