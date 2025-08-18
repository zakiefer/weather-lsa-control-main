from __future__ import annotations

from playwright.sync_api import Page


def test_change_basemap(base_url: str, page: Page) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    page.get_by_role("link", name="Map").click()
    page.wait_for_selector("text=Layers & styles")
    # Open the expander if collapsed
    page.get_by_text("Layers & styles").click()
    # Change basemap via selectbox with test id label prefix
    page.get_by_label("[tid]basemap_select:").select_option("Dark")
    page.get_by_role("button", name="[tid]basemap_apply:").click()


def test_adjust_opacity(base_url: str, page: Page) -> None:
    page.goto(base_url + "/Map", wait_until="domcontentloaded")
    page.wait_for_selector("text=Filters")
    # Open Radar form
    page.get_by_text("Radar").click()
    # Adjust slider by label
    slider = page.get_by_label("[tid]radar_opacity:")
    slider.focus()
    page.keyboard.press("ArrowRight")
