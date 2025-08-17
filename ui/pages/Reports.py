import json
from datetime import date, timedelta
from typing import Any, cast

import streamlit as st

st.set_page_config(page_title="Reports", page_icon="📈", layout="wide")

from ui._bootstrap import *  # noqa: F401,F403

require_auth()

from src.__main__ import get_credentials  # type: ignore
from src.config import settings as cfg  # type: ignore
from src.lsa_client import LSAClient  # type: ignore
from src.lsa_reporting import LSAReportingClient  # type: ignore
from ui.utils import DEFAULT_LEADS_MAP, prettify_headers, to_csv  # type: ignore
from ui.utils import project as ui_project

st.title("Reports")
st.caption("Local Services Ads lead details, performance, and account aggregates")


def _project(row: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    out = {}
    for c in cols:
        out[c] = row.get(c)
    return out


def _to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    # collect columns
    cols: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    import io

    buf = io.StringIO()
    buf.write(",".join(cols) + "\n")
    for r in rows:
        values = []
        for c in cols:
            v = r.get(c)
            if v is None:
                values.append("")
            else:
                s = str(v)
                if any(ch in s for ch in [",", "\n", '"']):
                    s = '"' + s.replace('"', '""') + '"'
                values.append(s)
        buf.write(",".join(values) + "\n")
    return buf.getvalue()


def _login_cid() -> str | None:
    cid = getattr(cfg, "LOGIN_CUSTOMER_ID", None)
    if cid:
        try:
            return str(int(str(cid).replace("-", "").strip()))
        except Exception:
            return str(cid)
    return None


with st.sidebar:
    st.subheader("Filters")
    today = date.today()
    default_start = today - timedelta(days=7)
    start_val = st.date_input("Start", value=default_start, max_value=today)
    # Streamlit may return a bare date or a tuple; normalize to date
    start = cast(date, start_val[0] if isinstance(start_val, (list, tuple)) and start_val else start_val)
    end_val = st.date_input("End", value=today, min_value=start, max_value=today)
    end = cast(date, end_val[0] if isinstance(end_val, (list, tuple)) and end_val else end_val)
    show_perf = st.toggle("Show performance (GAQL)", value=True)
    show_leads = st.toggle("Show detailed leads", value=True)
    show_agg = st.toggle("Show aggregates", value=True)
    st.markdown("---")
    only_charged = st.checkbox("Only charged leads", value=False)

cid = _login_cid()
if not cid:
    st.warning("No manager customer ID configured (GOOGLE_ADS_LOGIN_CUSTOMER_ID). Set it to enable reports.")
    st.stop()

# Prefer explicit LSA account id when available for detailed leads.
lsa_account = getattr(cfg, "LSA_ACCOUNT", "") or None

creds = get_credentials()
client = LSAReportingClient(creds)
ads_client = LSAClient(creds)


# Cache data fetches for a snappier UX
@st.cache_data(ttl=300, show_spinner=False)
def _cached_leads(_s: date, _e: date, _acct: str | None, _mgr: str | None):
    # Support both new (account_id) and older client signatures
    fn = getattr(client, "search_detailed_leads", None)
    if not callable(fn):
        return []
    try:
        import inspect  # type: ignore

        params = inspect.signature(fn).parameters
        kwargs: dict[str, Any] = {"start": _s, "end": _e, "page_size": 1000}
        if "account_id" in params:
            kwargs.update({"account_id": _acct, "manager_customer_id": _mgr})
        elif "accountId" in params:
            kwargs.update({"accountId": _acct, "manager_customer_id": _mgr})
        else:
            kwargs.update({"manager_customer_id": _mgr})
        res = fn(**kwargs)
    except TypeError:
        # Fallback to manager-only call
        res = fn(start=_s, end=_e, manager_customer_id=_mgr, page_size=1000)
    # Normalize to list[dict]
    out: list[dict[str, Any]] = []
    if isinstance(res, list):
        for item in res:
            if hasattr(item, "__dict__"):
                out.append(getattr(item, "__dict__"))
            elif isinstance(item, dict):
                out.append(item)
            else:
                out.append({"value": str(item)})
    return out


@st.cache_data(ttl=300, show_spinner=False)
def _cached_accounts(_s: date, _e: date, _acct: str | None, _mgr: str | None):
    fn = getattr(client, "search_account_reports", None)
    if not callable(fn):
        return []
    try:
        import inspect  # type: ignore

        params = inspect.signature(fn).parameters
        kwargs: dict[str, Any] = {"start": _s, "end": _e, "page_size": 1000}
        if "account_id" in params:
            kwargs.update({"account_id": _acct, "manager_customer_id": _mgr})
        elif "accountId" in params:
            kwargs.update({"accountId": _acct, "manager_customer_id": _mgr})
        else:
            kwargs.update({"manager_customer_id": _mgr})
        res = fn(**kwargs)
    except TypeError:
        res = fn(start=_s, end=_e, manager_customer_id=_mgr, page_size=1000)
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        arr = res.get("accountReports") if hasattr(res, "get") else None  # type: ignore[attr-defined]
        if isinstance(arr, list):
            return arr
        return [res]
    return []


@st.cache_data(ttl=300, show_spinner=False)
def _cached_gaql(query: str):
    _gaql = getattr(ads_client, "gaql", None)
    return _gaql(query) if callable(_gaql) else []


tabs = []
if show_leads:
    tabs.append("Detailed Leads")
if show_agg:
    tabs.append("Aggregates")
if show_perf:
    tabs.insert(0, "Performance")
if not tabs:
    st.info("Enable a section from the sidebar to view results.")
    st.stop()

# Create tabs in order
all_tabs = st.tabs(tabs)


if show_perf:
    t_index = tabs.index("Performance")
    with all_tabs[t_index]:
        st.subheader("Performance")
        # Build GAQL for date timeline
        q = (
            "SELECT metrics.impressions, metrics.clicks, metrics.cost_micros, segments.date "
            f"FROM customer WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        results = cast(list, _cached_gaql(q))
        if not results:
            # Fallback: approximate performance from LSA aggregates
            aggs = _cached_accounts(start, end, lsa_account, cid)
            if aggs:
                st.warning("No GAQL data; showing LSA aggregates approximation for the window.")
                try:
                    import pandas as pd  # type: ignore

                    # Use currentPeriod* fields when end == today; else just show totals table
                    row = aggs[0] if isinstance(aggs, list) and aggs else {}
                    curr_clicks = int(row.get("currentPeriodPhoneCalls") or 0)
                    curr_cost = float(row.get("currentPeriodTotalCost") or 0.0)
                    st.metric("Clicks (approx)", curr_clicks)
                    st.metric("Cost (approx)", f"${curr_cost:.2f}")
                    pretty_aggs = prettify_headers(aggs)
                    st.dataframe(pd.DataFrame(pretty_aggs), use_container_width=True, hide_index=True)
                except Exception:
                    st.dataframe(aggs, use_container_width=True, hide_index=True)
            else:
                st.info("No performance rows for the selected window.")
        else:
            rows = []
            for r in results:
                m = r.get("metrics", {})
                seg = r.get("segments", {})
                rows.append(
                    {
                        "date": seg.get("date"),
                        "clicks": int(m.get("clicks", 0) or 0),
                        "impressions": int(m.get("impressions", 0) or 0),
                        "cost": (int(m.get("costMicros", 0) or 0) / 1_000_000.0),
                    }
                )
            try:
                import altair as alt  # type: ignore
                import pandas as pd  # type: ignore

                df = pd.DataFrame(rows).sort_values("date")
                base = alt.Chart(df).encode(x="date:T")
                line_clicks = base.mark_line(color="#1a73e8").encode(y="clicks:Q")
                line_impr = base.mark_line(color="#ea4335").encode(y="impressions:Q")
                brush = alt.selection_interval(encodings=["x"])  # type: ignore
                layered = alt.layer(line_clicks, line_impr).resolve_scale(y="independent").add_params(brush)
                st.altair_chart(layered, use_container_width=True)  # type: ignore[arg-type]
                c1, c2, c3 = st.columns(3)
                c1.metric("Clicks", int(df["clicks"].sum()))
                c2.metric("Impressions", int(df["impressions"].sum()))
                c3.metric("Cost", f"${df['cost'].sum():.2f}")
                rows_dict = [dict(x) for x in df.to_dict(orient="records")]
                rows_typed: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in rows_dict]
                st.dataframe(prettify_headers(rows_typed), use_container_width=True, hide_index=True)
            except Exception:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            # Device breakdown and Day-of-week sections
            try:
                import altair as alt  # type: ignore
                import pandas as pd  # type: ignore

                with st.expander("Devices"):
                    qd = (
                        "SELECT segments.device, metrics.impressions, metrics.clicks, metrics.cost_micros "
                        f"FROM customer WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
                    )
                    dev_rows = cast(list, _cached_gaql(qd))
                    dnorm = []
                    for r in dev_rows:
                        seg = r.get("segments", {})
                        m = r.get("metrics", {})
                        dnorm.append(
                            {
                                "device": seg.get("device"),
                                "clicks": int(m.get("clicks", 0) or 0),
                                "impressions": int(m.get("impressions", 0) or 0),
                                "cost": (int(m.get("costMicros", 0) or 0) / 1_000_000.0),
                            }
                        )
                    if dnorm:
                        ddf = pd.DataFrame(dnorm)
                        st.bar_chart(ddf.set_index("device")["clicks"], use_container_width=True)
                        _rows = [dict(x) for x in ddf.to_dict(orient="records")]
                        _rows_typed: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in _rows]
                        st.dataframe(prettify_headers(_rows_typed), use_container_width=True, hide_index=True)
                    else:
                        st.info("No device data available.")
                with st.expander("Day of week"):
                    qw = (
                        "SELECT segments.day_of_week, metrics.impressions, metrics.clicks "
                        f"FROM customer WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
                    )
                    dow_rows = cast(list, _cached_gaql(qw))
                    wnorm = []
                    translate = {
                        "MONDAY": "Mon",
                        "TUESDAY": "Tue",
                        "WEDNESDAY": "Wed",
                        "THURSDAY": "Thu",
                        "FRIDAY": "Fri",
                        "SATURDAY": "Sat",
                        "SUNDAY": "Sun",
                    }
                    for r in dow_rows:
                        seg = r.get("segments", {})
                        m = r.get("metrics", {})
                        day = seg.get("dayOfWeek") or seg.get("day_of_week") or ""
                        wnorm.append(
                            {
                                "day": translate.get(day, day),
                                "impressions": int(m.get("impressions", 0) or 0),
                                "clicks": int(m.get("clicks", 0) or 0),
                            }
                        )
                    if wnorm:
                        wdf = pd.DataFrame(wnorm)
                        wdf = wdf.set_index("day").reindex(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]).fillna(0)
                        st.bar_chart(wdf["impressions"], use_container_width=True)
                        _wrows = [dict(x) for x in wdf.reset_index().to_dict(orient="records")]
                        _wrows_typed: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in _wrows]
                        st.dataframe(prettify_headers(_wrows_typed), use_container_width=True, hide_index=True)
                    else:
                        st.info("No day-of-week data available.")
            except Exception:
                pass

if show_leads:
    t_index = tabs.index("Detailed Leads")
    with all_tabs[t_index]:
        st.subheader("Detailed Leads")
        alt_opts = st.columns(3)
        try_mgr_only = alt_opts[0].checkbox("Try manager-only (ignore account)", value=False)
        use_numeric_id = alt_opts[1].checkbox("Use numeric account ID", value=False)
        acct_for_call = (
            None
            if try_mgr_only
            else (str(lsa_account).replace("accounts/", "") if use_numeric_id and lsa_account else lsa_account)
        )
        # Debug hint for query formation
        with st.expander("Debug: query inputs", expanded=False):
            cust_dbg = str(acct_for_call).replace("accounts/", "").replace("-", "").strip() if acct_for_call else None
            st.code(f"manager_customer_id:{cid};customer_id:{cust_dbg}" if cust_dbg else f"manager_customer_id:{cid}")
        rows = _cached_leads(start, end, acct_for_call, cid)
        uploaded_rows: list[dict[str, Any]] = []
        with st.expander("Upload Leads CSV (from LSA UI)", expanded=False):
            up = st.file_uploader("Upload Local Services Leads CSV export", type=["csv"], accept_multiple_files=False)
            if up is not None:
                try:
                    import io

                    import pandas as pd  # type: ignore

                    raw = up.read()
                    df = pd.read_csv(io.BytesIO(raw))

                    # Normalize column names: lower, strip, snake-ish
                    def _normcol(s: str) -> str:
                        return (
                            s.strip()
                            .lower()
                            .replace(" ", "_")
                            .replace("-", "_")
                            .replace("/", "_")
                            .replace("(", "")
                            .replace(")", "")
                        )

                    df.columns = [_normcol(c) for c in df.columns]

                    # Heuristic mapping
                    def pick(*names: str) -> str | None:
                        for n in names:
                            if n in df.columns:
                                return n
                        return None

                    col_id = pick("google_ads_lead_id", "lead_id", "googleadsleadid")
                    col_created = pick("lead_creation_timestamp", "created_at", "creation_time", "date", "time")
                    col_type = pick("lead_type", "type")
                    col_cat = pick("lead_category", "category", "job_type")
                    col_status = pick("charge_status", "status", "lead_status")
                    col_price = pick("lead_price", "price", "charged_amount")
                    col_currency = pick("currency_code", "currency")
                    col_postal = pick("postal_code", "zip", "zip_code")
                    col_phone = pick("consumer_phone_number", "phone", "phone_number")

                    def last4(x: Any) -> str | None:
                        s = "" if x is None else str(x)
                        digits = "".join(ch for ch in s if ch.isdigit())
                        return ("***" + digits[-4:]) if len(digits) >= 4 else None

                    for _, r in df.iterrows():
                        try:
                            uploaded_rows.append(
                                {
                                    "google_ads_lead_id": str(r.get(col_id)) if col_id else None,
                                    "account_id": getattr(cfg, "LSA_ACCOUNT", "").replace("accounts/", ""),
                                    "business_name": None,
                                    "created_at": str(r.get(col_created)) if col_created else None,
                                    "lead_type": str(r.get(col_type)) if col_type else None,
                                    "lead_category": str(r.get(col_cat)) if col_cat else None,
                                    "geo": None,
                                    "charge_status": str(r.get(col_status)) if col_status else None,
                                    "lead_price": (
                                        float(str(r.get(col_price)))
                                        if (col_price and pd.notna(r.get(col_price)))
                                        else None
                                    ),
                                    "currency_code": str(r.get(col_currency)) if col_currency else None,
                                    "dispute_status": None,
                                    "job_type": str(r.get(col_cat)) if col_cat else None,
                                    "postal_code": str(r.get(col_postal)) if col_postal else None,
                                    "phone_last4": last4(r.get(col_phone)),
                                }
                            )
                        except Exception:
                            continue
                    if uploaded_rows:
                        st.success(f"Loaded {len(uploaded_rows)} rows from CSV")
                except Exception as ie:
                    st.error(f"Failed to parse CSV: {ie}")
        # Prefer API rows; if none, show uploaded CSV rows
        if not rows and uploaded_rows:
            rows = uploaded_rows
        used_account: str | None = None
        if not rows:
            # Fallback: discover accounts from aggregates and try a few
            agg_rows = _cached_accounts(start, end, lsa_account, cid)
            cand_accts = []
            for r in agg_rows or []:
                acct = r.get("accountId") if isinstance(r, dict) else None
                if acct and acct not in cand_accts:
                    cand_accts.append(acct)
            for acct in cand_accts[:3]:
                retry = _cached_leads(start, end, acct, cid)
                if retry:
                    rows = retry
                    used_account = acct
                    break
        st.caption(f"Rows: {len(rows)}")
        if used_account:
            st.info(
                f"Showing leads for account {used_account}. To make this default, set LSA_ACCOUNT to this account ID in config.",
                icon="ℹ️",
            )
        if rows:
            # Build interactive filters
            lead_types = sorted({(r.get("lead_type") or "").strip() for r in rows if r.get("lead_type") is not None})
            categories = sorted(
                {(r.get("lead_category") or "").strip() for r in rows if r.get("lead_category") is not None}
            )
            statuses = sorted(
                {(r.get("charge_status") or "").strip() for r in rows if r.get("charge_status") is not None}
            )
            ft1, ft2, ft3 = st.columns(3)
            sel_types = ft1.multiselect("Lead type", options=lead_types, default=lead_types)
            sel_cats = ft2.multiselect("Category", options=categories, default=categories)
            default_status = [s for s in statuses if s.upper() == "CHARGED"] if only_charged else statuses
            sel_status = ft3.multiselect("Charge status", options=statuses, default=default_status)

            def _match(val: str | None, opts: list[str]) -> bool:
                vv = (val or "").strip()
                return (not opts) or vv in opts

            filtered = [
                r
                for r in rows
                if _match(r.get("lead_type"), sel_types)
                and _match(r.get("lead_category"), sel_cats)
                and _match(r.get("charge_status"), sel_status)
            ]

            # KPIs
            charged = [r for r in filtered if str(r.get("charge_status", "")).upper() == "CHARGED"]
            spend = sum(float(r.get("lead_price") or 0.0) for r in charged)
            c1, c2, c3 = st.columns(3)
            c1.metric("Charged leads", len(charged))
            c2.metric("Total (filtered)", len(filtered))
            c3.metric("Charged spend", f"${spend:.2f}")

            # Pivots by type/category
            try:
                import pandas as pd  # type: ignore

                with st.expander("Breakdown by Type/Category"):
                    df = pd.DataFrame(filtered)
                    piv1 = (
                        df.pivot_table(index="lead_type", values="google_ads_lead_id", aggfunc="count")
                        .rename(columns={"google_ads_lead_id": "leads"})
                        .reset_index()
                    )
                    piv2 = (
                        df.pivot_table(index="lead_category", values="google_ads_lead_id", aggfunc="count")
                        .rename(columns={"google_ads_lead_id": "leads"})
                        .reset_index()
                    )
                    c1, c2 = st.columns(2)
                    c1.dataframe(piv1.sort_values("leads", ascending=False), use_container_width=True, hide_index=True)
                    c2.dataframe(piv2.sort_values("leads", ascending=False), use_container_width=True, hide_index=True)
            except Exception:
                pass

            st.dataframe(prettify_headers(filtered, DEFAULT_LEADS_MAP), use_container_width=True, hide_index=True)
            # Timeline chart (charged vs others)
            try:
                import altair as alt  # type: ignore
                import pandas as pd  # type: ignore

                df = pd.DataFrame(filtered)
                # Normalize date
                if "created_at" in df.columns:
                    df["day"] = pd.to_datetime(df["created_at"]).dt.date
                else:
                    df["day"] = None
                df["charged"] = df["charge_status"].fillna("").str.upper().eq("CHARGED")
                agg = df.groupby(["day", "charged"], dropna=False).size().reset_index(name="leads")
                base = alt.Chart(agg).encode(x="day:T", y="leads:Q", color="charged:N")
                st.altair_chart(base.mark_area().interpolate("monotone"), use_container_width=True)
            except Exception:
                pass
            c1, c2 = st.columns(2)
            if c1.download_button(
                "Download CSV",
                data=to_csv(filtered),
                file_name=f"lsa_leads_{start}_{end}.csv",
                mime="text/csv",
            ):
                pass
            if c2.download_button(
                "Download JSON",
                data=json.dumps(filtered, indent=2),
                file_name=f"lsa_leads_{start}_{end}.json",
                mime="application/json",
            ):
                pass
        else:
            st.info("No leads for the selected window.")

if show_agg:
    t_index = tabs.index("Aggregates")
    with all_tabs[t_index]:
        st.subheader("Account Aggregates")
        rows = _cached_accounts(start, end, lsa_account, cid)
        st.caption(f"Accounts: {len(rows)}")
        if rows:
            # Lightly project interesting fields if present
            cols = [
                "accountId",
                "businessName",
                "currencyCode",
                "currentPeriodChargedLeads",
                "previousPeriodChargedLeads",
                "currentPeriodTotalCost",
                "previousPeriodTotalCost",
                "currentPeriodPhoneCalls",
                "previousPeriodPhoneCalls",
                "currentPeriodConnectedPhoneCalls",
                "previousPeriodConnectedPhoneCalls",
                "averageFiveStarRating",
                "totalReview",
            ]
            norm = [ui_project(r, cols) for r in rows]

            # Map to cleaner column titles and format values
            def _as_int(v):
                try:
                    return int(float(v))
                except Exception:
                    return v

            def _as_float(v):
                try:
                    return float(v)
                except Exception:
                    return None

            pretty_rows = []
            for r in norm:
                curr = r.get("currencyCode") or "USD"
                spend = _as_float(r.get("currentPeriodTotalCost"))
                spend_prev = _as_float(r.get("previousPeriodTotalCost"))
                pretty_rows.append(
                    {
                        "Account ID": r.get("accountId"),
                        "Business": r.get("businessName"),
                        "Currency": curr,
                        "Charged leads": _as_int(r.get("currentPeriodChargedLeads")),
                        "Prev charged": _as_int(r.get("previousPeriodChargedLeads")),
                        "Spend": (f"${spend:.2f}" if spend is not None and curr == "USD" else spend),
                        "Prev spend": (
                            f"${spend_prev:.2f}" if spend_prev is not None and curr == "USD" else spend_prev
                        ),
                        "Calls": _as_int(r.get("currentPeriodPhoneCalls")),
                        "Prev calls": _as_int(r.get("previousPeriodPhoneCalls")),
                        "Connected calls": _as_int(r.get("currentPeriodConnectedPhoneCalls")),
                        "Prev connected": _as_int(r.get("previousPeriodConnectedPhoneCalls")),
                        "Avg rating": _as_float(r.get("averageFiveStarRating")),
                        "Reviews": _as_int(r.get("totalReview")),
                    }
                )

            st.dataframe(pretty_rows, use_container_width=True, hide_index=True)
            try:
                # Simple CPL metric when present
                curr = norm[0].get("currencyCode")
                charged_leads = float(norm[0].get("currentPeriodChargedLeads") or 0)
                total_cost = float(norm[0].get("currentPeriodTotalCost") or 0)
                if charged_leads > 0:
                    st.metric("Cost per charged lead", f"{curr or 'USD'} {total_cost / charged_leads:.2f}")
            except Exception:
                pass
            if st.download_button(
                "Download JSON",
                data=json.dumps(rows, indent=2),
                file_name=f"lsa_account_reports_{start}_{end}.json",
                mime="application/json",
            ):
                pass
        else:
            st.info("No aggregate rows for the selected window.")
