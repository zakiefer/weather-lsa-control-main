import streamlit as st

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
import sys
from pathlib import Path

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ui._bootstrap import *  # noqa: F401,F403

require_auth()
from src.config import settings as cfg  # type: ignore
from src.db import delete_config_value, set_config_value  # type: ignore
from ui.http_client import clear_caches, get_status_snapshot  # type: ignore

st.title("Settings")

with st.expander("Flags"):
    cols = st.columns(3)
    with cols[0]:
        safe_mode = st.toggle("SAFE_MODE", value=bool(getattr(cfg, "SAFE_MODE", False)))
        if st.button("Apply SAFE_MODE"):
            set_config_value("SAFE_MODE", "true" if safe_mode else "false")
            st.success("Saved SAFE_MODE")
    with cols[1]:
        validate_gate = st.toggle("VALIDATE_GATE", value=bool(getattr(cfg, "VALIDATE_GATE", True)))
        if st.button("Apply VALIDATE_GATE"):
            set_config_value("VALIDATE_GATE", "true" if validate_gate else "false")
            st.success("Saved VALIDATE_GATE")
    with cols[2]:
        lsa_only = st.toggle(
            "REQUIRE_LOCAL_SERVICES_ONLY", value=bool(getattr(cfg, "REQUIRE_LOCAL_SERVICES_ONLY", False))
        )
        if st.button("Apply LSA-only"):
            set_config_value("REQUIRE_LOCAL_SERVICES_ONLY", "true" if lsa_only else "false")
            st.success("Saved LSA-only guard")

with st.expander("Thresholds"):
    cols = st.columns(3)
    with cols[0]:
        max_mut = st.number_input(
            "MAX_MUTATIONS_PER_DAY", min_value=0, value=int(getattr(cfg, "MAX_MUTATIONS_PER_DAY", 0))
        )
        if st.button("Apply MAX_MUTATIONS_PER_DAY"):
            set_config_value("MAX_MUTATIONS_PER_DAY", str(int(max_mut)))
            st.success("Saved")
    with cols[1]:
        hold_hours = st.number_input(
            "STORM_HOLD_TIME_HOURS", min_value=1, max_value=72, value=int(getattr(cfg, "STORM_HOLD_TIME_HOURS", 24))
        )
        if st.button("Apply HOLD HOURS"):
            set_config_value("STORM_HOLD_TIME_HOURS", str(int(hold_hours)))
            st.success("Saved")
    with cols[2]:
        qh = st.text_input("QUIET_HOURS", value=str(getattr(cfg, "QUIET_HOURS", "")))
        if st.button("Apply QUIET_HOURS"):
            if qh.strip():
                set_config_value("QUIET_HOURS", qh.strip())
            else:
                delete_config_value("QUIET_HOURS")
            st.success("Saved QUIET_HOURS")

st.caption("Values persist in DB app_config and are read by services at runtime. Avoid placing secrets here.")

with st.expander("Data caches & sources"):
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Clear HTTP caches"):
            clear_caches(clear_status=False)
            st.success("Cleared HTTP caches")
    with c2:
        if st.button("Clear caches + status"):
            clear_caches(clear_status=True)
            st.success("Cleared caches and status")
    st.divider()
    st.write("Recent fetch status (most recent first):")
    rows = get_status_snapshot()[:20]
    if not rows:
        st.info("No fetches recorded yet.")
    else:
        import pandas as pd  # type: ignore

        try:
            df = pd.DataFrame(rows)
            # Make it tidy
            if "last_attempt" in df.columns:
                import datetime as _dt

                df["last_attempt"] = df["last_attempt"].apply(
                    lambda t: _dt.datetime.fromtimestamp(t).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                )
            show_cols = [
                c for c in ["url", "status_code", "ok", "from_cache", "last_attempt", "error"] if c in df.columns
            ]
            st.dataframe(df[show_cols], use_container_width=True)
        except Exception:
            st.json(rows)
