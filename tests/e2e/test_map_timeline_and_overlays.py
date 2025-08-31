import os

import pytest
from playwright.sync_api import expect

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8520")


def folium_iframe(page):
    # Target the Streamlit Folium component iframe
    return page.frame_locator('iframe[src*="streamlit_folium"]').first


@pytest.mark.e2e
@pytest.mark.timeout(120)
def test_map_timeline_scrubber_updates_timestamp(page):
    # Navigate with SVG + fixtures for deterministic UI
    page.goto(f"{BASE}/?svg=1&spc_fixture=1&radar_fixture=1", wait_until="domcontentloaded")

    # Prefer helper timeline on parent if present, else use iframe one
    def get_label_text():
        # Try parent helper first
        lbl = page.locator("#rv_label")
        if lbl.count() > 0 and lbl.first().is_visible():
            return lbl.first.inner_text()
        # Fallback to Folium iframe helper/overlay
        return folium_iframe(page).locator("#rv_label").first.inner_text()

    # Capture starting label
    start = get_label_text()

    # Move timeline right a few steps; use either button or ArrowRight
    if page.locator("#rv_next").count() > 0:
        for _ in range(6):
            page.locator("#rv_next").first.click()
    else:
        for _ in range(6):
            page.keyboard.press("ArrowRight")

    page.wait_for_function(
        "([start, cur]) => cur !== start",
        arg=[start, get_label_text()],
    )


@pytest.mark.e2e
@pytest.mark.timeout(120)
def test_overlay_opacity_adjusts_layer_opacity(page):
    # Start with radar on and SVG mode for stable selectors
    page.goto(f"{BASE}/?radar=1&svg=1", wait_until="domcontentloaded")

    # Open the opacity drawer; it may be inside the iframe or mirrored on parent
    opened = False
    if page.locator("#op_drawer_open").count() > 0:
        page.locator("#op_drawer_open").first.click()
        opened = True
    else:
        btn = folium_iframe(page).locator("#op_drawer_open").first
        if btn.count() > 0:
            btn.click()
            opened = True
    assert opened, "Opacity drawer button not found"

    # Set radar opacity to ~30%
    target = "30"
    inp = page.locator("#op_rv")
    if inp.count() == 0:
        inp = folium_iframe(page).locator("#op_rv")
    expect(inp).to_be_visible()
    inp.fill(target)

    def read_effective_opacity():
        try:
            v = page.evaluate("() => window.localStorage.getItem('rv_opacity')")
            if v and str(v).isdigit():
                return int(v)
        except Exception:
            pass
        try:
            el = folium_iframe(page).locator("canvas, img.leaflet-tile").first
            if el.count() > 0:
                return int(float(el.evaluate("el => getComputedStyle(el).opacity")) * 100)
        except Exception:
            pass
        return None

    page.wait_for_function("(op) => op === 30", arg=read_effective_opacity())

