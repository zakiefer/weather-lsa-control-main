import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from streamlit.runtime.caching import cache_data

from ui.testids import testid

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
st.set_page_config(page_title="History", page_icon="📜", layout="wide")
from ui._bootstrap import *  # noqa: F401,F403

require_auth()
from src.db import get_conn  # type: ignore
from ui.utils import DEFAULT_HISTORY_MAP, prettify_headers  # type: ignore


def _fmt_short_local(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        t = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%m/%d %H:%M")
    except Exception:
        return str(ts)[:16]


@cache_data(ttl=30)
def load_history(days: int = 7, search: str | None = None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q = (
            "SELECT id, source, issued_at, areas, severity, hash, cap_id, effective_at "
            "FROM alerts WHERE datetime(issued_at) >= datetime(?) ORDER BY issued_at DESC"
        )
        res = cur.execute(q, (since,))
        rows = res.fetchall() if res is not None else []
        items = []
        for r in rows:
            items.append(
                {
                    "id": r[0],
                    "source": r[1],
                    "issued_at": _fmt_short_local(r[2]),
                    "areas": r[3],
                    "severity": r[4],
                    "hash": r[5],
                    "cap_id": r[6],
                    "effective_at": _fmt_short_local(r[7]),
                }
            )
        if search:
            import json as _json

            s = search.lower()
            items = [x for x in items if s in _json.dumps(x).lower()]
        return items
    finally:
        conn.close()


st.title("History")

# Optional auto-refresh to keep history fresh when monitoring
qp = getattr(st, "query_params", {}) or {}
qp_haf = qp.get("af") if isinstance(qp, dict) else None
qp_hr = qp.get("r") if isinstance(qp, dict) else None
if "hist_auto_refresh" not in st.session_state:
    st.session_state["hist_auto_refresh"] = (
        str(qp_haf).lower() not in ("0", "false", "no") if qp_haf is not None else False
    )
if "hist_refresh_sec" not in st.session_state:
    try:
        _rv = int(qp_hr) if qp_hr is not None else 120
    except Exception:
        _rv = 120
    st.session_state["hist_refresh_sec"] = max(30, min(600, _rv))

days = st.slider(testid("hist_days") + "Days", min_value=1, max_value=30, value=7)
search = st.text_input(testid("hist_search") + "Search", "")
auto_refresh = st.checkbox(
    testid("hist_auto_refresh") + "Auto-refresh", key="hist_auto_refresh", help="Refresh history table periodically"
)
refresh_sec = st.slider(
    testid("hist_refresh_sec") + "Refresh interval (sec)",
    min_value=30,
    max_value=600,
    value=st.session_state.get("hist_refresh_sec", 120),
    step=10,
    key="hist_refresh_sec",
)

items = load_history(days, search or None)
# Prettify headers for display
pretty = prettify_headers(items, DEFAULT_HISTORY_MAP)
st.dataframe(pretty, use_container_width=True, hide_index=True)

if items:
    st.download_button("Export JSON", data=json.dumps(items), file_name="alerts_history.json", mime="application/json")

# Persist query params (omit defaults) and inject optional auto-refresh
try:
    qp_out = {}
    _af = "1" if st.session_state.get("hist_auto_refresh", False) else "0"
    if _af != "0":
        qp_out["af"] = _af
    _r = str(int(st.session_state.get("hist_refresh_sec", 120)))
    if _r != "120":
        qp_out["r"] = _r
    if qp_out:
        st.query_params.update(qp_out)
except Exception:  # nosec B110: best-effort query param persistence; ignore in restrictive environments
    pass

if auto_refresh and refresh_sec:
    try:
        st.markdown(
            f"""
            <script>
            setTimeout(function() {{ window.location.reload(); }}, {int(st.session_state.get("hist_refresh_sec", refresh_sec)) * 1000});
            </script>
            """,
            unsafe_allow_html=True,
        )
    except Exception:  # nosec B110: ignore client-side injection failure; table still visible
        pass
