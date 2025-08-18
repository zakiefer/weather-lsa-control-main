from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
UI_ENTRY = ROOT / "ui" / "app.py"

STREAMLIT_URL = os.getenv("E2E_BASE_URL")  # e.g., http://localhost:8510


def _find_free_port(start: int = 8510, end: int = 8599) -> int:
    import socket

    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("no free port")


@contextmanager
def _streamlit_server() -> Iterator[str]:
    """Start Streamlit in a subprocess and yield the base URL. Kills on exit."""
    if not UI_ENTRY.exists():
        raise RuntimeError(f"Missing UI entrypoint: {UI_ENTRY}")
    if STREAMLIT_URL:
        yield STREAMLIT_URL
        return
    port = _find_free_port()
    env = os.environ.copy()
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    env.setdefault("E2E_TEST_IDS", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Ensure admin creds exist; tests can sign in
    env.setdefault("ADMIN_USERNAME", os.getenv("ADMIN_USERNAME", "admin"))
    env.setdefault("ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "Walmart2025!"))
    # Ensure run_ui.py binds the same port we probe
    env["STREAMLIT_PORT"] = str(port)

    cmd = [str(TOOLS / "run_ui.py")]
    proc = subprocess.Popen([str(ROOT / ".venv" / "bin" / "python"), *cmd], env=env)
    url = f"http://localhost:{port}"
    # Wait for server to become responsive
    ok = False
    for _ in range(120):
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=1.5) as resp:  # type: ignore
                ok = resp.status < 500
                if ok:
                    break
        except Exception:
            time.sleep(0.5)
    if not ok:
        proc.terminate()
        proc.wait(timeout=5)
        raise RuntimeError("Streamlit server did not start in time")
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        # headless by default; use E2E_HEADFUL=1 to override
        headless = os.getenv("E2E_HEADFUL", "0") not in {"1", "true", "yes", "on"}
        browser = p.chromium.launch(headless=headless)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    with _streamlit_server() as url:
        yield url


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    ctx = browser.new_context(storage_state=None)
    p = ctx.new_page()
    try:
        yield p
    finally:
        ctx.close()
