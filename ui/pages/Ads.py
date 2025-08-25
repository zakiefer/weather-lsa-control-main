import sys
from pathlib import Path

import streamlit as st
from streamlit.runtime.caching import cache_data

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
st.set_page_config(page_title="Ads", page_icon="📣", layout="wide")
from ui._bootstrap import *  # noqa: F401,F403

require_auth()
from urllib.parse import unquote_plus

from src.__main__ import get_credentials  # type: ignore
from src.config import settings as cfg  # type: ignore
from src.db import get_config_value, set_config_value  # type: ignore
from src.lsa_client import LSAClient  # type: ignore
from src.services.ads_service import AdsService  # type: ignore
from ui.testids import testid


# Simple local short time formatting helper for any future timestamps shown
def _fmt_short_local(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        from datetime import datetime, timezone

        t = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%m/%d %H:%M")
    except Exception:
        return str(ts)[:16]


@cache_data(ttl=20)
def fetch_campaigns(page_size: int = 100):
    try:
        creds = get_credentials()
        client = LSAClient(creds)
        return client.list_campaigns(page_size=page_size)
    except Exception:
        # In e2e or without creds, fail soft and show empty list
        return []


st.title("Ads")

# Context from deep link (e.g., from Map popups)
qp = getattr(st, "query_params", {}) or {}
if isinstance(qp, dict) and qp.get("from") == "map":
    event = unquote_plus(qp.get("event", "")) if qp.get("event") else ""
    fips = unquote_plus(qp.get("fips", "")) if qp.get("fips") else ""
    cap = unquote_plus(qp.get("cap", "")) if qp.get("cap") else ""
    st.info(f"From Map • Event: {event or '—'} • FIPS: {fips or '—'} • CAP: {cap or '—'}")

cols = st.columns(3)
with cols[0]:
    validate_only = st.toggle(
        testid("ads_validate_only") + "Validate-only", value=True, help="Use validateOnly when toggling status"
    )
with cols[1]:
    require_lsa = st.toggle(
        testid("ads_lsa_only_guard") + "LSA-only guard", value=True, help="Only allow Local Services campaigns"
    )
with cols[2]:
    kill_switch = st.toggle(testid("ads_kill_switch") + "Kill switch", value=bool(getattr(cfg, "KILL_SWITCH", False)))

if st.button(testid("ads_apply_kill") + "Apply kill switch"):
    set_config_value("KILL_SWITCH", "true" if kill_switch else "false")
    st.success("Saved kill switch. Services pick it up via settings/db.")

# Control whether to mutate LSA via Ads
eff_flag = True
try:
    dbv = get_config_value("lsa_mutate_via_ads_status")
    if isinstance(dbv, str) and dbv:
        t = dbv.strip().lower()
        if t in ("0", "false", "no"):
            eff_flag = False
        if t in ("1", "true", "yes"):
            eff_flag = True
    else:
        eff_flag = bool(getattr(cfg, "LSA_MUTATE_VIA_ADS_STATUS", True))
except Exception:
    eff_flag = bool(getattr(cfg, "LSA_MUTATE_VIA_ADS_STATUS", True))

st.subheader("LSA mutate behavior")
toggle_cols = st.columns(2)
with toggle_cols[0]:
    des = st.toggle(
        testid("ads_allow_mutate") + "Allow Ads.status changes for LSA",
        value=eff_flag,
        help="If off, we will validate-only and audit but not call live mutate for LSA.",
    )
with toggle_cols[1]:
    if st.button(testid("ads_save_mutate") + "Save LSA mutate setting"):
        set_config_value("lsa_mutate_via_ads_status", "true" if des else "false")
        st.success("Saved. Worker picks up immediately on next call.")

camps = fetch_campaigns()

if not camps:
    st.info("No campaigns or cannot list (check credentials/token and developer token access)")
else:
    for c in camps:
        with st.container(border=True):
            st.write(f"{c.get('name')} (ID {c.get('id')})")
            ch = c.get("channel")
            st.write(f"Status: {c.get('status')}  •  Channel: {ch or '—'}")
            if ch == "LOCAL_SERVICES":
                st.info(
                    "This campaign is a Local Services Ads (LSA) campaign. In some accounts, the 'Your ad is on'\n"
                    "toggle and schedule in the LSA Profile & budget page control serving independently of the\n"
                    "Google Ads campaign status. If you see mismatched statuses across UIs, rely on the LSA\n"
                    "Profile page for the source of truth.",
                )
            new = st.selectbox(
                testid("ads_set_status") + "Set status",
                options=["ENABLED", "PAUSED"],
                index=0,
                key=f"sel_{c.get('id')}",
            )
            if st.button(testid("ads_apply_status") + "Apply", key=f"btn_{c.get('id')}"):
                creds = get_credentials()
                svc = AdsService(creds)
                res = svc.client.set_campaign_status(new, validate_only=validate_only)
                ok = bool(res)
                st.success("Validate-only OK" if ok and validate_only else ("Applied" if ok else "Failed"))
