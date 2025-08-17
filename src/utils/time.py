"""Time utilities.

Centralizes QUIET_HOURS parsing used across monitor, worker, and CLI.
"""

from __future__ import annotations

import datetime as _dt


def within_quiet_hours(qh: str | None) -> bool:
    """Return True if current local time falls within QUIET_HOURS window.

    QUIET_HOURS format: "HH:MM-HH:MM". Handles windows that cross midnight.
    Returns False on any parse error or when qh is falsy.
    """
    if not qh:
        return False
    try:
        now = _dt.datetime.now().time()
        start_s, end_s = (s.strip() for s in qh.split("-"))
        start = _dt.time.fromisoformat(start_s)
        end = _dt.time.fromisoformat(end_s)

        def _within(start_t: _dt.time, end_t: _dt.time, t: _dt.time) -> bool:
            return (start_t <= t <= end_t) if start_t <= end_t else (t >= start_t or t <= end_t)

        return _within(start, end, now)
    except Exception:
        return False
