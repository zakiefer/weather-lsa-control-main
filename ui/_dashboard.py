import sys
from pathlib import Path

import streamlit as st
from streamlit.runtime.caching import cache_data

# Ensure `src` is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from src.db import (  # type: ignore
    ensure_schema,
    get_queue_stats,
    list_area_cooldowns,
    list_recent_errors,
    summarize_error_codes,
)


@cache_data(ttl=15)
def _qs():
    ensure_schema()
    return get_queue_stats()


@cache_data(ttl=30)
def _recent_errs(limit: int = 25):
    ensure_schema()
    return list_recent_errors(limit)


@cache_data(ttl=30)
def _cooldowns(limit: int = 50):
    ensure_schema()
    return list_area_cooldowns(limit)


@cache_data(ttl=30)
def _top_errors(limit: int = 10):
    ensure_schema()
    return summarize_error_codes(limit)


def render_dashboard():
    st.title("Dashboard")
    qs = _qs()
    c = st.columns(4)
    c[0].metric("Queued", qs.get("queued", 0))
    c[1].metric("Running", qs.get("running", 0))
    c[2].metric("Done", qs.get("done", 0))
    c[3].metric("Error", qs.get("error", 0))

    st.markdown("---")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Top error codes/messages (7d)")
        top = _top_errors(10)
        if top:
            try:
                import pandas as pd  # type: ignore

                df = pd.DataFrame(top)
                st.bar_chart(df.set_index("key")["count"], use_container_width=True)
            except Exception:
                st.dataframe(top, use_container_width=True, hide_index=True)
        else:
            st.info("No recent errors recorded.")
    with c2:
        st.subheader("Recent area cooldowns")
        cds = _cooldowns(20)
        if cds:
            try:
                from ui.utils import prettify_headers  # type: ignore

                st.dataframe(prettify_headers(cds), use_container_width=True, hide_index=True)
            except Exception:
                st.dataframe(cds, use_container_width=True, hide_index=True)
        else:
            st.info("No recent cooldown changes.")

    st.subheader("Recent errors")
    errs = _recent_errs(50)
    if errs:
        try:
            from ui.utils import prettify_headers  # type: ignore

            st.dataframe(prettify_headers(errs), use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(errs, use_container_width=True, hide_index=True)
    else:
        st.info("No recent error entries.")
