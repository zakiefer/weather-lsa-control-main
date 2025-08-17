from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any  # noqa: F401

from .http_client import find_latest_status


@dataclass
class SourceSpec:
    match: str
    fresh_secs: int
    label: str


SOURCES: dict[str, SourceSpec] = {
    # Data JSON endpoints used by overlays
    "spc": SourceSpec("spc_outlooks.geojson", 600, "SPC Outlooks"),
    "eq": SourceSpec("earthquakes", 600, "Earthquakes"),
    "trp": SourceSpec("CurrentStorms.json", 600, "Tropical"),
    "wf": SourceSpec("Wildland_Fire_Perimeters", 600, "Wildfires"),
    "lsr": SourceSpec("lsr.php", 120, "Storm Reports"),
    "hist": SourceSpec("api.weather.gov/alerts", 120, "History"),
}


def _age_str(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        s = int(max(0, delta.total_seconds()))
        if s < 60:
            return f"{s}s ago"
        m = s // 60
        if m < 60:
            return f"{m}m ago"
        h = m // 60
        return f"{h}h ago"
    except Exception:
        return "n/a"


def overlay_status(source_key: str) -> tuple[str, str]:
    """
    Return (color_hex, tooltip) for the given overlay source.
    Colors: green=#2ecc71, amber=#f39c12, red=#e74c3c, gray=#95a5a6
    """
    spec = SOURCES.get(source_key)
    if not spec:
        return ("#95a5a6", "unknown source")
    st = find_latest_status(lambda url, s: spec.match in url)
    if not st:
        return ("#95a5a6", f"{spec.label}: no recent requests")
    ok = bool(st.get("ok"))
    age = _age_str(float(st.get("last_attempt") or 0))
    code = st.get("status_code")
    url = st.get("url")
    # Freshness bucket
    try:
        last_ts = float(st.get("last_attempt") or 0)
    except Exception:
        last_ts = 0.0
    try:
        fresh_limit = spec.fresh_secs * 1.5
    except Exception:
        fresh_limit = 300.0
    now_s = datetime.now(timezone.utc).timestamp()
    is_fresh = (now_s - last_ts) <= fresh_limit
    if not ok:
        color = "#e74c3c"  # red
    elif is_fresh:
        color = "#2ecc71"  # green
    else:
        color = "#f39c12"  # amber
    tip = f"{spec.label}: {'OK' if ok else 'Error'} ({code if code else 'n/a'}) — {age}\n{url}"
    return (color, tip)


def status_pip_html(source_key: str) -> str:
    color, tip = overlay_status(source_key)
    # Small circle with border and title tooltip
    safe_tip = tip.replace("'", "&#39;")
    style = (
        "width:12px; height:12px; border-radius:50%; "
        f"background:{color}; "
        "border:1px solid rgba(0,0,0,0.15); display:inline-block; margin-top:6px;"
    )
    return f"<div title='{safe_tip}' style='{style}'></div>"
