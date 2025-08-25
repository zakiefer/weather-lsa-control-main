from __future__ import annotations

import re

from playwright.sync_api import Page


def _maybe_sign_in(page: Page, base_url: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    # If dashboard is visible, we're already signed in
    try:
        page.wait_for_selector("text=Dashboard", timeout=2000)
        return
    except Exception:
        pass
    # Otherwise, login if fields are present
    if page.get_by_label("[tid]login_username: Username", exact=True).count() > 0:
        page.get_by_label("[tid]login_username: Username", exact=True).fill("admin")
        page.get_by_label("[tid]login_password: Password", exact=True).fill("admin")
        page.get_by_role("button", name="[tid]login_submit: Sign in", exact=True).click()
        page.wait_for_selector("text=Dashboard")


def test_change_basemap(base_url: str, page: Page) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    _maybe_sign_in(page, base_url)
    # Ensure radar is loaded so map container stabilizes
    page.goto(base_url + "/Map?rd=1", wait_until="domcontentloaded")
    # Wait for parent readiness to avoid iframe remount races
    try:
        page.wait_for_function(
            "() => document.body && (document.body.getAttribute('data-map-ready-parent')==='1' || document.body.getAttribute('data-sidebar-ready')==='1')",
            timeout=10000,
        )
    except Exception:
        pass
    # Open the expander if collapsed (expanders render as buttons)
    try:
        page.get_by_role("button", name=re.compile(r"Layers \& styles", re.I)).click()
    except Exception:
        pass
    # Change basemap via Streamlit selectbox: prefer label; ensure dropdown opens; fallback to combobox click
    try:
        lab = page.get_by_label(re.compile(r"^\[tid\]basemap_select: Basemap$|^Basemap$", re.I))
        lab.scroll_into_view_if_needed()
        lab.click()
    except Exception:
        pass
    # If listbox didn't open, click the combobox itself
    try:
        page.get_by_role("listbox").wait_for(state="visible", timeout=800)
    except Exception:
        cb = page.get_by_role("combobox", name=re.compile(r"Basemap", re.I))
        try:
            cb.scroll_into_view_if_needed()
        except Exception:
            pass
        cb.click()
        # small retry loop to tolerate Streamlit rerun and ensure list appears
        for _ in range(20):
            try:
                page.get_by_role("listbox").wait_for(state="visible", timeout=400)
                break
            except Exception:
                page.wait_for_timeout(100)
    # Wait for the options list and pick a single known option (prefer Dark); fall back to keyboard selection
    try:
        page.get_by_role("listbox").wait_for(state="visible", timeout=15000)
        # Prefer Dark option when available
        opt = page.get_by_role("option", name=re.compile(r"^Dark$", re.I))
        if opt.count() == 0:
            # Fallback to any option that is not currently selected
            opt = page.get_by_role("option").nth(1)
        opt.click()
    except Exception:
        # Keyboard-only fallback: open, move, confirm
        page.keyboard.press("Enter")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
    page.get_by_role("button", name=re.compile(r"^(\[tid\])?basemap_apply: Apply basemap$", re.I)).click()
    page.wait_for_timeout(300)


def test_adjust_opacity(base_url: str, page: Page) -> None:
    page.goto(base_url + "/Map", wait_until="domcontentloaded")
    # Wait for deterministic readiness rather than text
    try:
        page.wait_for_function(
            "() => document.body && (document.body.getAttribute('data-map-ready-parent')==='1' || document.body.getAttribute('data-sidebar-ready')==='1')",
            timeout=10000,
        )
    except Exception:
        pass
    # Adjust slider by role/name; avoid exact match and use regex; fall back to label
    slider = page.get_by_role("slider", name=re.compile(r"Opacity", re.I))
    if slider.count() == 0:
        slider = page.get_by_label("[tid]radar_opacity: Opacity")
    slider.scroll_into_view_if_needed()
    slider.focus()
    page.keyboard.press("ArrowRight")
