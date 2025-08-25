import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import streamlit as st
from streamlit.runtime.caching import cache_data

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Page setup
st.set_page_config(page_title="Live", page_icon="🌩️", layout="wide")

from ui._bootstrap import *  # noqa: F401,F403

require_auth()
from src.__main__ import get_credentials  # type: ignore
from src.weather_monitor import WeatherMonitor  # type: ignore
from ui.utils import prettify_headers  # type: ignore


@cache_data(ttl=30)
def fetch_live_features(states: list[str]):
    creds = get_credentials()
    mon = WeatherMonitor(creds)
    feats: list[dict] = []
    for s in states:
        try:
            feats.extend(mon._fetch_alerts_for_state(s))
        except Exception:
            # Continue on per-state failure
            pass
    return feats


from ui.testids import testid

st.title("Live")

# Read query params to prefill filter, states, and refresh controls (from deep-links)
qp = getattr(st, "query_params", {}) or {}
prefill = qp.get("flt") if isinstance(qp, dict) else None
prefill_states = None
if isinstance(qp, dict) and (qp.get("states") or qp.get("st")):
    try:
        val = qp.get("states") or qp.get("st")
        prefill_states = [s for s in str(val).split(",") if s]
    except Exception:
        prefill_states = None
qp_af = None
qp_r = None
if isinstance(qp, dict):
    qp_af = qp.get("af")
    qp_r = qp.get("r")

allowed_states = ["IN", "IL", "KY"]
if "ls_states" not in st.session_state:
    st.session_state["ls_states"] = [s for s in (prefill_states or allowed_states) if s in allowed_states]
states = st.multiselect(
    testid("live_states") + "States",
    options=allowed_states,
    key="ls_states",
    help="Areas to query from NWS active alerts",
)
if "ls_filter" not in st.session_state:
    st.session_state["ls_filter"] = prefill or ""
features_for_opts = fetch_live_features(st.session_state["ls_states"]) if st.session_state.get("ls_states") else []
# Build a list of unique, non-empty event names (type-safe for sorting)
event_opts = sorted(
    {
        str((f.get("properties") or {}).get("event"))
        for f in features_for_opts
        if (f.get("properties") or {}).get("event")
    }
)
flt = st.text_input(
    testid("live_filter") + "Filter event contains", key="ls_filter", placeholder="e.g., Tornado Warning"
)
if event_opts:
    st.selectbox(
        testid("live_event_pick") + "Or pick an event", options=["—"] + event_opts, index=0, key="ls_event_pick"
    )
    if st.session_state.get("ls_event_pick") and st.session_state.get("ls_event_pick") != "—":
        st.session_state["ls_filter"] = st.session_state.get("ls_event_pick")
colz = st.columns(2)
with colz[0]:
    if flt:
        href = f"?page=Map&event={quote_plus(flt)}&trig=0&autoz=1"
        st.markdown(
            f"[🗺️ Open Map with this event]({href})",
            help="Opens Map page with event pre-selected",
            unsafe_allow_html=True,
        )
with colz[1]:
    st.button(testid("live_zoom_map") + "Zoom Map to data (on Map)")
if st.button(testid("live_refresh") + "Refresh"):
    st.experimental_rerun()

# Auto-refresh controls with session state + deep link defaults
if "ls_auto_refresh" not in st.session_state:
    st.session_state["ls_auto_refresh"] = str(qp_af).lower() not in ("0", "false", "no") if qp_af is not None else True
if "ls_refresh_sec" not in st.session_state:
    try:
        rv = int(qp_r) if qp_r is not None else 60
    except Exception:
        rv = 60
    st.session_state["ls_refresh_sec"] = max(15, min(300, rv))
auto_refresh = st.checkbox(testid("live_auto_refresh") + "Auto-refresh", key="ls_auto_refresh")
refresh_sec = st.slider(
    testid("live_refresh_sec") + "Refresh interval (sec)",
    min_value=15,
    max_value=300,
    value=st.session_state.get("ls_refresh_sec", 60),
    step=5,
    key="ls_refresh_sec",
)

features = fetch_live_features(states)

# Flatten table
rows = []
for f in features:
    p = f.get("properties", {})
    # format time short local
    eff = p.get("effective") or p.get("onset") or p.get("sent") or p.get("published")
    try:
        if eff:
            t = eff.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            eff_short = dt.astimezone().strftime("%m/%d %H:%M")
        else:
            eff_short = "—"
    except Exception:
        eff_short = eff or "—"
    rows.append(
        {
            "event": p.get("event"),
            "severity": p.get("severity"),
            "urgency": p.get("urgency"),
            "certainty": p.get("certainty"),
            "area": p.get("areaDesc"),
            "effective": eff_short,
            "id": p.get("id") or p.get("capId") or p.get("cap_id"),
        }
    )

if flt:
    rows = [r for r in rows if flt.lower() in (r.get("event") or "").lower()]
# Prettify display headers
st.dataframe(prettify_headers(rows), use_container_width=True, hide_index=True)

st.caption(f"Total: {len(rows)} active alerts")
if auto_refresh and refresh_sec:
    try:
        st.markdown(
            f"""
            <script>
            setTimeout(function() {{ window.location.reload(); }}, {int(st.session_state.get("ls_refresh_sec", refresh_sec)) * 1000});
            </script>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass

# Sync query params and show shareable link (omit defaults for brevity)
try:
    qp_out = {}
    _flt = st.session_state.get("ls_filter", "")
    if _flt:
        qp_out["flt"] = _flt
    _states = ",".join(st.session_state.get("ls_states", allowed_states))
    if _states != ",".join(allowed_states):
        qp_out["st"] = _states
    _af = "1" if st.session_state.get("ls_auto_refresh", True) else "0"
    if _af != "1":
        qp_out["af"] = _af
    _r = str(int(st.session_state.get("ls_refresh_sec", 60)))
    if _r != "60":
        qp_out["r"] = _r
    if qp_out:
        st.query_params.update(qp_out)
except Exception:
    pass

try:
    _flt = st.session_state.get("ls_filter", "")
    _states = ",".join(st.session_state.get("ls_states", allowed_states))
    _af = "1" if st.session_state.get("ls_auto_refresh", True) else "0"
    _r = str(int(st.session_state.get("ls_refresh_sec", 60)))
    parts = ["?page=Live"]
    if _flt:
        parts.append(f"flt={quote_plus(_flt)}")
    if _states != ",".join(allowed_states):
        parts.append(f"st={quote_plus(_states)}")
    if _af != "1":
        parts.append(f"af={_af}")
    if _r != "60":
        parts.append(f"r={_r}")
    rel_link = "&".join(parts)
    st.caption("Share this view")
    show_ls_link = st.checkbox("Show link", value=False, key="ls_show_link")
    if show_ls_link:
        st.code(rel_link)
        st.caption("Tip: use the copy icon on the code box to copy the link.")
    st.link_button("Open share link", rel_link, type="secondary")
except Exception:
    pass
