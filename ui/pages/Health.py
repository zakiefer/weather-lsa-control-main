import sys
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Health", page_icon="🧺", layout="wide")
from streamlit.runtime.caching import cache_data

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ui._bootstrap import *  # noqa: F401,F403

require_auth()
from src.health import compute_readiness  # type: ignore

st.title("Health")


@cache_data(ttl=15)
def readiness():
    return compute_readiness()


info = readiness()

cols = st.columns(4)
cols[0].metric("Ready", "yes" if info.get("ready") else "no")
cols[1].metric("DB", "ok" if info.get("db_ok") else "fail")
cols[2].metric("Creds", "ok" if info.get("creds_ok") else "missing")
cols[3].metric("Breaker", "open" if info.get("breaker_open") else "closed")

with st.expander("Details", expanded=False):
    st.json(info)

with st.expander("Logs tail (weather_monitor.log)"):
    log_path = Path(__file__).resolve().parents[2] / "logs" / "weather_monitor.log"
    try:
        content = log_path.read_text(encoding="utf-8")
        lines = content.strip().splitlines()[-400:]
        st.code("\n".join(lines))
    except Exception as e:
        st.info(str(e))

from ui.testids import testid

if st.button(testid("health_refresh") + "Refresh now"):
    st.experimental_rerun()
