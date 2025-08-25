"""
Stable test IDs for UI elements. Use testid("name") around labels/text in Streamlit components
so Playwright can reliably select them. Test IDs render only when env E2E_TEST_IDS is truthy
to avoid visual noise for end users.

Example:
    st.button(testid("login_submit") + "Sign in")

In HTML/JS snippets, include data-testid attributes when possible.
"""

from __future__ import annotations

import os

PREFIX = "[tid]"


def testid(name: str) -> str:
    # Enable test IDs when explicitly requested or when running under pytest
    if os.getenv("E2E_TEST_IDS", "0").lower() in {"1", "true", "yes", "on"} or os.getenv("PYTEST_CURRENT_TEST"):
        return f"{PREFIX}{name}: "
    return ""
