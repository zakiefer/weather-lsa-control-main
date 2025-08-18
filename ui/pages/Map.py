import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from urllib.parse import quote_plus

import streamlit as st
from streamlit.runtime.caching import cache_data

# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Page setup
st.set_page_config(page_title="Map", page_icon="🗺️", layout="wide")
from ui._bootstrap import *  # noqa: F401,F403

require_auth()
import folium
from folium import plugins
from streamlit_folium import st_folium
from ui.testids import testid

from src.__main__ import get_credentials  # type: ignore
from src.config.settings import (
    ALLOWED_CERTAINTY,
    ALLOWED_SEVERITIES,
    ALLOWED_URGENCY,
    CENTER_LAT,
    CENTER_LON,
    RULES_FILE,
    TARGET_COUNTIES,
    TARGET_COUNTY_FIPS,
    TRIGGER_EVENTS,
)

# type: ignore
from src.rules import evaluate as eval_rules
from src.rules import load_rules  # type: ignore
from src.weather_monitor import WeatherMonitor  # type: ignore
from ui.http_client import clear_caches, get_status_snapshot  # type: ignore
from ui.map_layers import (
    add_earthquakes,
    add_historical_timeline,
    add_lsr_layers,
    add_spc_outlooks,
    add_tropical,
    add_wildfires,
)
from ui.overlay_status import status_pip_html  # type: ignore

SEVERITY_COLOR = {
    "Extreme": "#d73027",
    "Severe": "#fc8d59",
    "Moderate": "#fee08b",
    "Minor": "#d9ef8b",
}

# Sidebar alias for concise usage
SB = st.sidebar

# App constants
ALL_STATES = ["IN", "IL", "KY"]

# Event category mapping for quick filter toggles
CATEGORY_MAP = {
    # Severe
    "Tornado Warning": "Severe",
    "Severe Thunderstorm Warning": "Severe",
    "Severe Weather Statement": "Severe",
    "Tornado Watch": "Severe",
    "Severe Thunderstorm Watch": "Severe",
    # Flood
    "Flash Flood Warning": "Flood",
    "Flood Warning": "Flood",
    "Areal Flood Warning": "Flood",
    "Flood Advisory": "Flood",
    # Tropical
    "Hurricane Warning": "Tropical",
    "Tropical Storm Warning": "Tropical",
    "Storm Surge Warning": "Tropical",
    "Hurricane Watch": "Tropical",
    "Tropical Storm Watch": "Tropical",
    # Winter
    "Winter Storm Warning": "Winter",
    "Blizzard Warning": "Winter",
    "Ice Storm Warning": "Winter",
    "Winter Weather Advisory": "Winter",
    # Marine
    "Gale Warning": "Marine",
    "Storm Warning": "Marine",
    "Small Craft Advisory": "Marine",
}


def _cat_for_event(evt: str) -> str:
    return CATEGORY_MAP.get(evt, "Other")


# --- Helpers used by this page ---
def _fmt_time_short(v: str | None) -> str:
    if not v:
        return "—"
    try:
        t = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%m/%d %H:%M")
    except Exception:
        return str(v)


def _extract_county_fips(props: dict) -> list[str]:
    geocode = (props.get("geocode") or {}) if isinstance(props, dict) else {}
    fips6 = geocode.get("FIPS6") or geocode.get("FIPS") or []
    out: list[str] = []
    for code in fips6:
        code = str(code)
        if len(code) >= 5:
            out.append(code[-5:])
    return out


def _alert_matches_filters(props: dict) -> bool:
    try:
        event = props.get("event")
        if event not in TRIGGER_EVENTS:
            return False
        severity = props.get("severity")
        urgency = props.get("urgency")
        certainty = props.get("certainty")
        if ALLOWED_SEVERITIES and severity and severity not in ALLOWED_SEVERITIES:
            return False
        if ALLOWED_URGENCY and urgency and urgency not in ALLOWED_URGENCY:
            return False
        if ALLOWED_CERTAINTY and certainty and certainty not in ALLOWED_CERTAINTY:
            return False
        return True
    except Exception:
        return False


def _first_polygon_centroid(alert: dict) -> tuple[float | None, float | None]:
    try:
        geom = alert.get("geometry") or {}
        coords = []
        if geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [])
        elif geom.get("type") == "MultiPolygon":
            polys = geom.get("coordinates", [])
            coords = polys[0] if polys else []
        if not (coords and coords[0]):
            return (None, None)
        ring = coords[0]
        lat_sum = lon_sum = 0.0
        n = len(ring)
        for lon, lat in ring:
            lat_sum += lat
            lon_sum += lon
        return (lat_sum / n, lon_sum / n)
    except Exception:
        return (None, None)


def _nearest_rainviewer_frame(min_ago: int | None) -> int | None:
    """Return RainViewer frame timestamp (ms since epoch) rounded to ~10 min, or 0 for latest."""
    try:
        if not min_ago or int(min_ago) <= 0:
            return 0
        t = datetime.utcnow() - timedelta(minutes=int(min_ago))
        # round down to 10-minute boundary
        sec = int(t.timestamp())
        sec = (sec // 600) * 600
        return sec * 1000
    except Exception:
        return None


@cache_data(ttl=30)
def fetch_alerts(states: list[str]):
    creds = get_credentials()
    mon = WeatherMonitor(creds)
    feats = []
    for s in states:
        feats.extend(mon._fetch_alerts_for_state(s))
    return feats


@cache_data(ttl=60)
def load_rules_cached(path: str | None):
    try:
        if path:
            return load_rules(path)
    except Exception:
        pass
    return []


# Query params (deep-linking). Keep short keys and fallbacks.
qp = getattr(st, "query_params", {}) or {}
qp_event = qp.get("event") if isinstance(qp, dict) else None
qp_events_raw = qp.get("ev") if isinstance(qp, dict) else None
qp_trig = (qp.get("tg") if isinstance(qp, dict) else None) or (qp.get("trig") if isinstance(qp, dict) else None)
qp_autoz = qp.get("az") if isinstance(qp, dict) else None
# History/timeline deep-link params
qp_hist = (qp.get("ht") if isinstance(qp, dict) else None) or (qp.get("hist") if isinstance(qp, dict) else None)
qp_hh = qp.get("hh") if isinstance(qp, dict) else None  # hours back
qp_hsel = (qp.get("hs") if isinstance(qp, dict) else None) or (qp.get("hsel") if isinstance(qp, dict) else None)
qp_htr = (qp.get("hr") if isinstance(qp, dict) else None) or (qp.get("htr") if isinstance(qp, dict) else None)
# Radar overlay params
qp_radar = (qp.get("rd") if isinstance(qp, dict) else None) or (qp.get("radar") if isinstance(qp, dict) else None)
qp_rsrc = (qp.get("rs") if isinstance(qp, dict) else None) or (qp.get("radsrc") if isinstance(qp, dict) else None)
qp_rop = (qp.get("ro") if isinstance(qp, dict) else None) or (qp.get("rop") if isinstance(qp, dict) else None)
# Trigger-type filter params
qp_tr_rules = qp.get("tr") if isinstance(qp, dict) else None
qp_tr_filters = qp.get("tf") if isinstance(qp, dict) else None
# LSR (storm reports) params
qp_lsr = qp.get("lsr") if isinstance(qp, dict) else None
qp_lsrh = qp.get("lsrh") if isinstance(qp, dict) else None
qp_lsr_hail = qp.get("lsrhail") if isinstance(qp, dict) else None
qp_lsr_wind = qp.get("lsrwind") if isinstance(qp, dict) else None
qp_lsr_tor = qp.get("lsrtor") if isinstance(qp, dict) else None
qp_lsr_path = qp.get("lsrpath") if isinstance(qp, dict) else None
# States and Fast mode params
qp_st = qp.get("st") if isinstance(qp, dict) else None
qp_fm = qp.get("fm") if isinstance(qp, dict) else None
# Radar archive, Satellite, GLM lightning, SPC
qp_ra = qp.get("ra") if isinstance(qp, dict) else None
qp_ram = qp.get("ram") if isinstance(qp, dict) else None
qp_rtl = qp.get("rtl") if isinstance(qp, dict) else None
qp_rts = qp.get("rts") if isinstance(qp, dict) else None
qp_rll = qp.get("rll") if isinstance(qp, dict) else None
qp_rah = qp.get("rah") if isinstance(qp, dict) else None
qp_sat = qp.get("sat") if isinstance(qp, dict) else None
qp_sati = qp.get("sati") if isinstance(qp, dict) else None
qp_glm = qp.get("glm") if isinstance(qp, dict) else None
qp_spc = qp.get("spc") if isinstance(qp, dict) else None
qp_spcd = qp.get("spcd") if isinstance(qp, dict) else None
# New specialty overlays
qp_eq = qp.get("eq") if isinstance(qp, dict) else None
qp_eqmin = qp.get("eqmin") if isinstance(qp, dict) else None
qp_trp = qp.get("trp") if isinstance(qp, dict) else None
qp_wf = qp.get("wf") if isinstance(qp, dict) else None
qp_lat = qp.get("lat") if isinstance(qp, dict) else None
qp_lon = qp.get("lon") if isinstance(qp, dict) else None
qp_z = qp.get("z") if isinstance(qp, dict) else None

SB.header("Map Options")

# Prefer client-side toggles to avoid full reruns that cause flashing
if "reduce_flash" not in st.session_state:
    st.session_state["reduce_flash"] = True
if st.session_state.get("reduce_flash", True):
    SB.info(
        "Layer changes are applied in-map to prevent flashing. Use the Layers control (top-right) and the Opacity button.",
    )

# Apply radar preferences from localStorage to URL once per tab, then reload
try:
    st.markdown(
        """
        <script>
        (function(){
            try {
                if (window.sessionStorage.getItem('radar_prefs_applied') === '1') { return; }
                var url = new URL(window.location.href);
                var changed = false;
                function setParam(k, v){ if (v==null) return; if (url.searchParams.get(k) !== String(v)) { url.searchParams.set(k, String(v)); changed = true; } }
                var rd = localStorage.getItem('radar_on');
                var rs = localStorage.getItem('radar_source');
                var ro = localStorage.getItem('rv_opacity');
                var rah = localStorage.getItem('ra_hide_live');
                if (rd === '1' || rd === '0') { setParam('rd', rd); }
                if (rs && (rs === 'iem' || rs === 'rv')) { setParam('rs', rs); }
                if (ro && !isNaN(parseInt(ro))) { var _ro = Math.max(10, Math.min(100, parseInt(ro))); setParam('ro', String(_ro)); }
                if (rah === '1' || rah === '0') { setParam('rah', rah); }
                if (changed) { window.sessionStorage.setItem('radar_prefs_applied','1'); window.location.replace(url.toString()); }
                else { window.sessionStorage.setItem('radar_prefs_applied','1'); }
            } catch (e) { /* ignore */ }
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    pass

# Apply additional overlay/UI preferences from localStorage to URL once per tab, then reload
try:
    st.markdown(
        """
        <script>
        (function(){
            try {
                if (window.sessionStorage.getItem('ui_prefs_applied') === '1') { return; }
                var url = new URL(window.location.href);
                var changed = false;
                function setParam(k, v){ if (v==null) return; if (url.searchParams.get(k) !== String(v)) { url.searchParams.set(k, String(v)); changed = true; } }
                // Satellite
                var sat_true = localStorage.getItem('sat_true');
                var sat_ir = localStorage.getItem('sat_ir');
                if (sat_true === '1' || sat_true === '0') { setParam('sat', sat_true); }
                if (sat_ir === '1' || sat_ir === '0') { setParam('sati', sat_ir); }
                // GLM
                var glm_on = localStorage.getItem('glm_on');
                if (glm_on === '1' || glm_on === '0') { setParam('glm', glm_on); }
                // SPC
                var spc_on = localStorage.getItem('spc_on');
                var spc_day = localStorage.getItem('spc_day');
                if (spc_on === '1' || spc_on === '0') { setParam('spc', spc_on); }
                if (spc_day && ['1','2','3'].indexOf(spc_day) !== -1) { setParam('spcd', spc_day); }
                // Basemap
                var base = localStorage.getItem('basemap');
                if (base && ['Light','Dark','OSM','Satellite'].indexOf(base) !== -1) { setParam('base', base); }
                // Categories (short tokens: severe,flood,tropical,winter,marine,other)
                var cat = localStorage.getItem('cat_filters');
                if (cat && cat.length > 0) { setParam('cat', cat); }
                // States (CSV of IN,IL,KY)
                var st = localStorage.getItem('states');
                if (st && st.length > 0) { setParam('st', st); }
                // Specialty overlays (optional)
                var eq = localStorage.getItem('eq_on');
                if (eq === '1' || eq === '0') { setParam('eq', eq); }
                var eqmin = localStorage.getItem('eq_minmag');
                if (eqmin && !isNaN(parseFloat(eqmin))) { setParam('eqmin', String(eqmin)); }
                var trp = localStorage.getItem('trp_on');
                if (trp === '1' || trp === '0') { setParam('trp', trp); }
                var wf = localStorage.getItem('wf_on');
                if (wf === '1' || wf === '0') { setParam('wf', wf); }
                if (changed) { window.sessionStorage.setItem('ui_prefs_applied','1'); window.location.replace(url.toString()); }
                else { window.sessionStorage.setItem('ui_prefs_applied','1'); }
            } catch (e) { /* ignore */ }
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    pass

# Basemap and category query params
qp_base = qp.get("base") if isinstance(qp, dict) else None
qp_catf = qp.get("cat") if isinstance(qp, dict) else None

# Basemap options
basemap_options = {
    "Light": {
        "tiles": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "CartoDB Light",
    },
    "Dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attr": "CartoDB Dark",
    },
    "OSM": {
        "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "OpenStreetMap contributors",
    },
    "Satellite": {
        # Using ESRI World Imagery tiles
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Imagery",
    },
}

if "map_basemap" not in st.session_state:
    st.session_state["map_basemap"] = qp_base if qp_base in basemap_options else "Light"

with SB.expander("Layers & styles", expanded=True):
    if not st.session_state.get("reduce_flash", True):
        with SB.form("basemap_form"):
            st.selectbox(
                testid("basemap_select") + "Basemap",
                options=list(basemap_options.keys()),
                index=list(basemap_options.keys()).index(st.session_state.get("map_basemap", "Light")),
                key="map_basemap",
                help="Change the base map style",
            )
            st.form_submit_button(testid("basemap_apply") + "Apply basemap")
    else:
        st.caption("Tip: switch basemaps via the Layers control on the map to avoid reruns.")

# Seed selected states from query params
if "states_sel" not in st.session_state:
    if qp_st:
        _seed_states = [s.strip().upper() for s in str(qp_st).split(",") if s]
        st.session_state["states_sel"] = [s for s in _seed_states if s in ALL_STATES] or ALL_STATES
    else:
        st.session_state["states_sel"] = ALL_STATES
states = SB.multiselect(
    "States", options=ALL_STATES, default=st.session_state.get("states_sel", ALL_STATES), key="states_sel"
)
features = fetch_alerts(states)

# Build dropdown options from actual events in fetched alerts
events_set = {
    str((f.get("properties") or {}).get("event")) for f in features if (f.get("properties") or {}).get("event")
}
# Include all configured trigger events as selectable options, even if not currently present
events_set.update(TRIGGER_EVENTS)
unique_events = sorted(list(events_set))

# Use session_state to support deep-linked defaults (multi-select)
if "map_events" not in st.session_state:
    if qp_events_raw:
        seed = [e for e in str(qp_events_raw).split(",") if e]
    elif qp_event and qp_event in unique_events:
        seed = [qp_event]
    else:
        seed = unique_events
    st.session_state["map_events"] = seed
if "map_only_triggered" not in st.session_state:
    # Default to only triggered unless explicitly disabled via query param
    st.session_state["map_only_triggered"] = qp_trig not in ("0", "false", "no")
if "map_auto_zoom" not in st.session_state:
    st.session_state["map_auto_zoom"] = qp_autoz not in ("0", "false", "no")
if "map_show_hist" not in st.session_state:
    st.session_state["map_show_hist"] = qp_hist not in ("0", "false", "no") if qp_hist is not None else False
if "map_hist_hours" not in st.session_state:
    try:
        st.session_state["map_hist_hours"] = max(1, min(72, int(qp_hh))) if qp_hh is not None else 24
    except Exception:
        st.session_state["map_hist_hours"] = 24
if "map_hist_only_selected" not in st.session_state:
    st.session_state["map_hist_only_selected"] = qp_hsel not in ("0", "false", "no") if qp_hsel is not None else True
if "map_hist_only_triggers" not in st.session_state:
    st.session_state["map_hist_only_triggers"] = qp_htr not in ("0", "false", "no") if qp_htr is not None else False
if "map_radar" not in st.session_state:
    # Default radar ON unless explicitly disabled via query param
    st.session_state["map_radar"] = qp_radar not in ("0", "false", "no") if qp_radar is not None else True

with SB.expander("Filters", expanded=True):
    selected_events = st.multiselect(
        "Events",
        options=unique_events,
        default=st.session_state.get("map_events", unique_events),
        key="map_events",
        help="Toggle events like layers",
    )
    # Category filters similar to pro weather apps
    if "map_cat_filters" not in st.session_state:
        # Parse qp_catf like cat=flood,tropical
        def _seed_cat():
            if qp_catf:
                raw = [c.strip() for c in str(qp_catf).split(",") if c]
                # Map short aliases
                alias = {
                    "tornado": "Severe",
                    "severe": "Severe",
                    "flood": "Flood",
                    "tropical": "Tropical",
                    "winter": "Winter",
                    "marine": "Marine",
                    "other": "Other",
                }
                return {alias.get(x.lower(), x) for x in raw}
            # Default: all on
            return {"Severe", "Flood", "Tropical", "Winter", "Marine", "Other"}

        st.session_state["map_cat_filters"] = _seed_cat()
    cat_cols = SB.columns(2)
    cats = ["Severe", "Flood", "Tropical", "Winter", "Marine", "Other"]
    new_cats = set(st.session_state.get("map_cat_filters", set()))
    for i, cat in enumerate(cats):
        with cat_cols[i % 2]:
            checked = cat in new_cats
            val = st.checkbox(cat, value=checked, key=f"cat_{i}")
            if val:
                new_cats.add(cat)
            else:
                new_cats.discard(cat)
    st.session_state["map_cat_filters"] = new_cats
    only_triggered = st.checkbox(
        "Only triggered", key="map_only_triggered", help="Show alerts that trigger rules or filters"
    )
# Seed Fast mode from query param
if "map_fast_mode" not in st.session_state:
    st.session_state["map_fast_mode"] = qp_fm not in ("0", "false", "no") if qp_fm is not None else True
fast_mode = SB.checkbox(
    "Fast mode",
    value=st.session_state.get("map_fast_mode", True),
    help="Use clustering and simplified rendering for speed",
    key="map_fast_mode",
)
tc = SB.columns(2)
with tc[0]:
    if "map_trig_rules" not in st.session_state:
        # Default true; allow deep link override via tr=0
        st.session_state["map_trig_rules"] = (
            qp_tr_rules not in ("0", "false", "no") if qp_tr_rules is not None else True
        )
    trig_rules = st.checkbox("Rules", key="map_trig_rules")
with tc[1]:
    if "map_trig_filters" not in st.session_state:
        # Default true; allow deep link override via tf=0
        st.session_state["map_trig_filters"] = (
            qp_tr_filters not in ("0", "false", "no") if qp_tr_filters is not None else True
        )
    trig_filters = st.checkbox("Filters", key="map_trig_filters")
auto_zoom = SB.checkbox("Auto-zoom", key="map_auto_zoom")
if not st.session_state.get("reduce_flash", True):
    with SB.form("radar_form"):
        SB.checkbox("Radar", key="map_radar")
        radar_cols = SB.columns(2)
        with radar_cols[0]:
            if "map_radar_source" not in st.session_state:
                # Accept short key rs, fallback to legacy radsrc
                _rs = qp_rsrc if qp_rsrc is not None else "iem"
                st.session_state["map_radar_source"] = _rs if _rs in ("iem", "rv") else "iem"
            st.selectbox(
                testid("radar_source") + "Radar source",
                options=["iem", "rv"],
                index=0 if (st.session_state.get("map_radar_source", "iem") == "iem") else 1,
                format_func=lambda v: "IEM NEXRAD" if v == "iem" else "RainViewer",
                key="map_radar_source",
            )
        with radar_cols[1]:
            if "map_radar_opacity" not in st.session_state:
                try:
                    _rop = int(str(qp_rop)) if qp_rop is not None else 60
                except Exception:
                    _rop = 60
                st.session_state["map_radar_opacity"] = max(10, min(100, _rop))
            # Avoid reruns that reset Leaflet layer toggles: when the unified opacity drawer is on,
            # don't render the sidebar slider. Use the in-map control instead.
            # Always offer a slider in non-reduce_flash mode so users can adjust explicitly
            st.slider(
                testid("radar_opacity") + "Opacity",
                min_value=10,
                max_value=100,
                value=st.session_state.get("map_radar_opacity", 60),
                step=5,
                key="map_radar_opacity",
            )
        st.form_submit_button(testid("radar_apply") + "Apply radar")
    # Always read the applied state after the form
    radar_on = bool(st.session_state.get("map_radar", False))
else:
    # Use persisted state only; toggle layers via the in-map control to avoid flashing
    radar_on = bool(st.session_state.get("map_radar", False))
    if "map_radar_source" not in st.session_state:
        _rs = qp_rsrc if qp_rsrc is not None else "iem"
        st.session_state["map_radar_source"] = _rs if _rs in ("iem", "rv") else "iem"
    radar_source = st.session_state.get("map_radar_source", "iem")
    if "map_radar_opacity" not in st.session_state:
        try:
            _rop = int(str(qp_rop)) if qp_rop is not None else 60
        except Exception:
            _rop = 60
        st.session_state["map_radar_opacity"] = max(10, min(100, _rop))
    st.metric("Radar opacity", f"{int(st.session_state.get('map_radar_opacity', 60))}%")
    SB.caption("Toggle radar and source in the Layers control to prevent full reruns.")

SPC_COLORS = {
    "TSTM": "#a1d99b",
    "MRGL": "#74c476",
    "SLGT": "#31a354",
    "ENH": "#ffcc00",
    "MDT": "#ff7f00",
    "HIGH": "#e31a1c",
}
with SB.expander("Advanced"):
    auto_refresh = st.checkbox("Auto-refresh", value=True, help="Refresh the map periodically", key="adv_auto_refresh")
    # Option to enable a unified Layer Opacity drawer and hide individual HUDs
    if "opacity_drawer" not in st.session_state:
        st.session_state["opacity_drawer"] = True
    st.checkbox(
        "Unified opacity drawer",
        value=st.session_state.get("opacity_drawer", True),
        key="opacity_drawer",
        help="Show one consolidated opacity control and hide the small per-layer HUDs",
    )
    st.checkbox(
        "Reduce flashing (client-side toggles)",
        value=st.session_state.get("reduce_flash", True),
        key="reduce_flash",
        help="Hide frequently changed sidebar widgets and use in-map controls to avoid reruns.",
    )
    # Reset map preferences: clears persisted localStorage/sessionStorage keys and deep-link params
    if st.button("Reset map preferences", type="secondary", help="Clear saved map state and deep-link params"):
        try:
            # Best-effort: clear commonly used session_state keys for this page
            for k in [
                # Core map
                "map_events",
                "map_only_triggered",
                "map_auto_zoom",
                "map_fast_mode",
                "map_trig_rules",
                "map_trig_filters",
                "map_basemap",
                "states_sel",
                "map_cat_filters",
                # Radar and archive
                "map_radar",
                "map_radar_source",
                "map_radar_opacity",
                "ra_hide_live",
                "ra_timeline_on",
                "ra_speed",
                "ra_loop",
                "ra_frame_idx",
                # Satellite/GLM
                "sat_opacity",
                "glm_opacity",
                # SPC & overlays
                "spc_on_cb",
                "eq_on_cb",
                "trp_on_cb",
                "wf_on_cb",
                "eq_min_mag",
                # History timeline
                "map_show_hist",
                "map_hist_hours",
                "map_hist_only_selected",
                "map_hist_only_triggers",
                # LSR
                "lsr_on_cb",
                "lsr_hours_back",
            ]:
                if k in st.session_state:
                    del st.session_state[k]
        except Exception:
            pass
        st.success("Preferences cleared. Reloading…")
        try:
            st.markdown(
                """
                <script>
                (function(){
                    try {
                        var lsKeys = [
                            'radar_on','radar_source','rv_opacity','ra_hide_live',
                            'sat_true','sat_ir','sat_opacity',
                            'glm_on','glm_opacity',
                            'spc_on','spc_day',
                            'basemap','cat_filters','states',
                            'eq_on','eq_minmag','trp_on','wf_on'
                        ];
                        var ssKeys = ['radar_prefs_applied','ui_prefs_applied'];
                        lsKeys.forEach(function(k){ try { localStorage.removeItem(k); } catch(e){} });
                        ssKeys.forEach(function(k){ try { sessionStorage.removeItem(k); } catch(e){} });
                        // Reset query params to a clean Map page
                        var url = new URL(window.location.href);
                        url.search = '?page=Map';
                        window.location.replace(url.toString());
                    } catch(e) { /* ignore */ }
                })();
                </script>
                """,
                unsafe_allow_html=True,
            )
        except Exception:
            pass
    sub = st.toggle("Show data source status", value=False, help="Show recent fetch status and a quick cache clear")
    if sub:
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Clear HTTP caches (quick)"):
                clear_caches(clear_status=False)
                st.success("Cleared caches")
        with c2:
            if st.button("Clear caches + status (full)"):
                clear_caches(clear_status=True)
                st.success("Cleared caches + status")
        rows = get_status_snapshot()[:10]
        if rows:
            try:
                import pandas as _pd

                df = _pd.DataFrame(rows)
                show_cols = [c for c in ["url", "status_code", "ok", "from_cache", "error"] if c in df.columns]
                st.dataframe(df[show_cols], use_container_width=True, height=220)
            except Exception:
                st.json(rows)
    refresh_sec = st.slider("Refresh (sec)", min_value=15, max_value=300, value=60, step=5, key="adv_refresh_sec")
    force_zoom = st.button("Zoom to data")

rules = load_rules_cached(RULES_FILE)

# Build map with selected basemap; add all base layers for toggle
# Use deep-linked center/zoom if provided; else configured; else default
_lat: float | None = None
_lon: float | None = None
_zoom: int = 7
try:
    if qp_lat is not None and qp_lon is not None:
        _lat = float(qp_lat)
        _lon = float(qp_lon)
        _zoom = int(qp_z) if qp_z is not None else 7
    else:
        try:
            _lat = float(CENTER_LAT) if CENTER_LAT is not None else None
            _lon = float(CENTER_LON) if CENTER_LON is not None else None
        except Exception:
            _lat, _lon = (None, None)
        if _lat is None or _lon is None:
            try:
                dc = _default_center()
            except Exception:
                dc = (37.97, -87.57)
            _lat, _lon = dc
        _zoom = 7
except Exception:
    _lat, _lon, _zoom = (37.97, -87.57, 7)

# Normalize to concrete floats for the map (guarantee non-None)
lat: float = float(_lat) if _lat is not None else 37.97
lon: float = float(_lon) if _lon is not None else -87.57
zoom: int = int(_zoom or 7)

m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None, prefer_canvas=True, control_scale=True)
try:
    selected_base = st.session_state.get("map_basemap", "Light")
    for name, meta in basemap_options.items():
        folium.TileLayer(
            tiles=meta["tiles"],
            attr=meta["attr"],
            name=name,
            control=True,
            show=(name == selected_base),
        ).add_to(m)
except Exception:
    pass
# Radar archive scrubber (collect UI first so we can conditionally show live radar)
with SB.expander("Radar archive (last ~2 hours)"):
    ra_on = st.checkbox(
        "Archive mode",
        value=qp_ra not in ("0", "false", "no") if qp_ra is not None else False,
        help="Show a past radar frame and optionally hide live radar layers",
    )
    # Seed timeline setting from rtl
    _rtl_seed = qp_rtl not in ("0", "false", "no") if qp_rtl is not None else True
    ra_timeline = st.checkbox(
        "Use bottom timeline (playable)",
        value=_rtl_seed,
        key="ra_timeline_on",
        help="Adds a bottom scrubber with play/pause to step through the last ~2 hours",
    )
    if st.session_state.get("ra_timeline_on", True):
        # Seed speed and loop from rts (float) and rll (bool)
        try:
            _seed_speed = float(qp_rts) if qp_rts is not None else 1.0
        except Exception:
            _seed_speed = 1.0
        st.session_state.setdefault("ra_speed", max(0.5, min(3.0, _seed_speed)))
        _seed_loop = qp_rll not in ("0", "false", "no") if qp_rll is not None else True
        st.session_state.setdefault("ra_loop", _seed_loop)
        st.slider(
            "Timeline speed",
            min_value=0.5,
            max_value=3.0,
            value=float(st.session_state.get("ra_speed", 1.0)),
            step=0.1,
            key="ra_speed",
            help="Playback speed multiplier (0.5x–3x)",
        )
        st.checkbox(
            "Loop",
            value=bool(st.session_state.get("ra_loop", True)),
            key="ra_loop",
            help="When off, playback stops at the oldest frame",
        )
    # Convert optional minutes seed (ram) to a 0..12 frame index (10-min steps)
    try:
        _seed_min = int(qp_ram) if qp_ram is not None else 20
    except Exception:
        _seed_min = 20
    _seed_idx = max(0, min(12, round(_seed_min / 10)))
    if not st.session_state.get("ra_timeline_on", True):
        ra_frame = st.slider(
            "Time (last 2h)",
            min_value=0,
            max_value=12,
            value=_seed_idx,
            step=1,
            key="ra_frame_idx",
            help="0 = now, 12 = 120 minutes ago; step is 10 minutes",
        )
    else:
        # When timeline is active, still compute a frame index for initial selection
        st.session_state.setdefault("ra_frame_idx", _seed_idx)
        ra_frame = int(st.session_state.get("ra_frame_idx", _seed_idx))
    # Seed hide-live from rah param (default True)
    if "ra_hide_live" not in st.session_state:
        st.session_state["ra_hide_live"] = qp_rah not in ("0", "false", "no") if qp_rah is not None else True
    ra_hide_live = st.checkbox(
        "Hide live radar while in archive", value=bool(st.session_state.get("ra_hide_live", True)), key="ra_hide_live"
    )
    ra_minutes = int(st.session_state.get("ra_frame_idx", ra_frame)) * 10
    ra_ts = _nearest_rainviewer_frame(ra_minutes)
    st.caption(f"~{ra_minutes} min ago")

# Add radar overlay tiles (both sources present; live layers can be hidden when archive mode is on)
try:
    sel = st.session_state.get("map_radar_source") or "iem"
    op = float(st.session_state.get("map_radar_opacity", 60)) / 100.0
    live_show = bool(radar_on and not (ra_on and st.session_state.get("ra_hide_live", True)))
    radar_layer_vars: list[str] = []
    # IEM NEXRAD (XYZ / EPSG:900913)
    _iem_layer = folium.TileLayer(
        tiles="https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png",
        attr="IEM Nexrad",
        name="NEXRAD (IEM)",
        overlay=True,
        control=True,
        show=bool(live_show and sel == "iem"),
        opacity=op,
    )
    _iem_layer.add_to(m)
    try:
        radar_layer_vars.append(_iem_layer.get_name())
    except Exception:
        pass
    # RainViewer Latest (XYZ) — use 256px tiles for broader compatibility
    _rv_layer = folium.TileLayer(
        tiles="https://tilecache.rainviewer.com/v2/radar/0/256/{z}/{x}/{y}/2/1_1.png",
        attr="RainViewer",
        name="Radar (RainViewer Latest)",
        overlay=True,
        control=True,
        show=bool(live_show and sel == "rv"),
        opacity=op,
        # Render up to z=20 by upscaling native tiles (native z≈12)
        max_native_zoom=12,
        max_zoom=20,
        min_zoom=2,
    )
    _rv_layer.add_to(m)
    try:
        radar_layer_vars.append(_rv_layer.get_name())
    except Exception:
        pass
    # Add the selected archive frame or timeline frames
    if ra_on:
        if st.session_state.get("ra_timeline_on", True):
            speed = float(st.session_state.get("ra_speed", 1.0) or 1.0)
            try:
                interval_ms = max(200, int(900 / max(0.1, speed)))
            except Exception:
                interval_ms = 900
            is_loop = bool(st.session_state.get("ra_loop", True))
            rv_minutes = [i * 10 for i in range(0, 13)]
            rv_ts_list = [_nearest_rainviewer_frame(mins) for mins in rv_minutes]
            frame_var_names: list[str] = []
            for idx, ts in enumerate(rv_ts_list):
                if ts is None:
                    continue
                layer = folium.TileLayer(
                    tiles=f"https://tilecache.rainviewer.com/v2/radar/{ts}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
                    attr="RainViewer",
                    name=f"RV t-{rv_minutes[idx]}m",
                    overlay=True,
                    control=False,
                    show=False,
                    opacity=op,
                    max_native_zoom=12,
                    max_zoom=20,
                    min_zoom=2,
                )
                layer.add_to(m)
                try:
                    frame_var_names.append(layer.get_name())
                except Exception:
                    pass
            try:
                map_js_var = m.get_name()
            except Exception:
                map_js_var = "map"
            labels_js = json.dumps([f"~{mins}m" for mins in rv_minutes])
            init_idx = int(st.session_state.get("ra_frame_idx", ra_frame))
            frame_names_json = json.dumps(frame_var_names)
            timeline_html = f"""
                        <div id='rv_timeline_wrap' style='position: fixed; bottom: 10px; left: 50%; transform: translateX(-50%); z-index: 9999; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'>
                            <div style='display:flex; align-items:center; gap:8px; min-width: 420px;'>
                                <button id='rv_play' style='padding:2px 8px;'>Play</button>
                                <button id='rv_pause' style='padding:2px 8px;'>Pause</button>
                                <button id='rv_prev' style='padding:2px 6px;' title='Step back (←)'>◀</button>
                                <button id='rv_next' style='padding:2px 6px;' title='Step forward (→)'>▶</button>
                                <button id='rv_now' style='padding:2px 6px;' title='Jump to now (0m)'>Now</button>
                                <button id='rv_oldest' style='padding:2px 6px;' title='Jump to oldest (~120m)'>Oldest</button>
                                <input id='rv_slider' type='range' min='0' max='{len(rv_minutes) - 1}' step='1' value='{init_idx}' style='width:260px;'>
                                <span id='rv_label' style='min-width:70px; text-align:center; font-weight:600;'></span>
                                <span style='margin-left:8px; color:#444;'>Speed: {speed:.1f}x · Loop: {"On" if is_loop else "Off"}</span>
                            </div>
                        </div>
                        <script>
                            (function() {{
                                var map = window['{map_js_var}'];
                                var names = {frame_names_json};
                                var rvLayers = [];
                                try {{ map.eachLayer(function(l){{ try {{ if(l && l.options && l.options.name && names.indexOf(l.options.name)!==-1) rvLayers.push(l); }} catch(e){{}} }}); }} catch(e){{}}
                                var labels = {labels_js};
                                var slider = document.getElementById('rv_slider');
                                var label = document.getElementById('rv_label');
                                var playBtn = document.getElementById('rv_play');
                                var pauseBtn = document.getElementById('rv_pause');
                                var prevBtn = document.getElementById('rv_prev');
                                var nextBtn = document.getElementById('rv_next');
                                var nowBtn = document.getElementById('rv_now');
                                var oldestBtn = document.getElementById('rv_oldest');
                                var idx = {init_idx};
                                var timer = null;
                                var intervalMs = {interval_ms};
                                var isLoop = {str(is_loop).lower()};

                                function showFrame(i) {{
                                    idx = i;
                                    rvLayers.forEach(function(l, k) {{
                                        try {{
                                            if (k === i) {{ if (!map.hasLayer(l)) l.addTo(map); }}
                                            else {{ if (map.hasLayer(l)) map.removeLayer(l); }}
                                        }} catch (e) {{}}
                                    }});
                                    if (label) label.textContent = labels[i] || '';
                                }}

                                if (slider) {{
                                    slider.addEventListener('input', function() {{ showFrame(parseInt(slider.value)); }});
                                }}
                                if (prevBtn) {{
                                    prevBtn.addEventListener('click', function() {{
                                        var next = idx - 1; if (next < 0) next = 0; slider.value = next; showFrame(next);
                                    }});
                                }}
                                if (nextBtn) {{
                                    nextBtn.addEventListener('click', function() {{
                                        var next = idx + 1; if (next >= rvLayers.length) next = rvLayers.length - 1; slider.value = next; showFrame(next);
                                    }});
                                }}
                                if (nowBtn) {{
                                    nowBtn.addEventListener('click', function() {{ slider.value = 0; showFrame(0); }});
                                }}
                                if (oldestBtn) {{
                                    oldestBtn.addEventListener('click', function() {{ var last = rvLayers.length - 1; slider.value = last; showFrame(last); }});
                                }}
                                // Keyboard shortcuts: ← → space
                                document.addEventListener('keydown', function(e) {{
                                    if (!slider) return;
                                    if (e.code === 'ArrowLeft') {{ e.preventDefault(); var n = Math.max(0, idx - 1); slider.value = n; showFrame(n); }}
                                    if (e.code === 'ArrowRight') {{ e.preventDefault(); var n = Math.min(rvLayers.length - 1, idx + 1); slider.value = n; showFrame(n); }}
                                    if (e.code === 'Space') {{ e.preventDefault(); if (!timer) {{ playBtn && playBtn.click(); }} else {{ pauseBtn && pauseBtn.click(); }} }}
                                }});
                                if (playBtn) {{
                                    playBtn.addEventListener('click', function() {{
                                        if (timer) return;
                                        timer = setInterval(function() {{
                                            var next = idx + 1;
                                            if (next >= rvLayers.length) {{
                                                if (isLoop) next = 0; else {{ clearInterval(timer); timer = null; return; }}
                                            }}
                                            slider.value = next;
                                            showFrame(next);
                                        }}, intervalMs);
                                    }});
                                }}
                                if (pauseBtn) {{
                                    pauseBtn.addEventListener('click', function() {{ if (timer) {{ clearInterval(timer); timer = null; }} }});
                                }}
                                // Initialize
                                showFrame(idx);
                            }})();
                        </script>
                        """
            m.get_root().add_child(folium.Element(timeline_html))
            # Include timeline frame layers in the generic radar opacity controller
            try:
                radar_layer_vars.extend(frame_var_names)
            except Exception:
                pass
        elif ra_ts is not None:
            _arch_layer = folium.TileLayer(
                tiles=f"https://tilecache.rainviewer.com/v2/radar/{ra_ts}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
                attr="RainViewer",
                name=f"Radar Archive (~{ra_minutes}m ago)",
                overlay=True,
                control=True,
                show=True,
                opacity=op,
                max_native_zoom=12,
                max_zoom=20,
                min_zoom=2,
            )
            _arch_layer.add_to(m)
            try:
                radar_layer_vars.append(_arch_layer.get_name())
            except Exception:
                pass
    # Add an in-map client-side opacity control (no Streamlit rerun)
    if not st.session_state.get("opacity_drawer", True):
        try:
            if (radar_on or ra_on) and radar_layer_vars:
                try:
                    map_js_var = m.get_name()
                except Exception:
                    map_js_var = "map"
                # We collect layer display names (Leaflet LayerControl names). Build a JS list of names
                layers_names_json = json.dumps(radar_layer_vars)
                init_pct = int(st.session_state.get("map_radar_opacity", 60))
                _tmpl = Template("""
                        <div id='rv_op_wrap' style='position: fixed; top: 100px; right: 10px; z-index: 9999; background: rgba(255,255,255,0.95); padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'>
                            <div style='font-weight:600; margin-bottom:4px;'>Radar Opacity</div>
                            <input id='rv_op_slider' type='range' min='10' max='100' step='5' value='$init_pct' style='width:140px;'>
                            <span id='rv_op_val' style='margin-left:6px;'>$init_pct%</span>
                        </div>
                        <script>
                            (function(){
                                try {
                                    var map = window['$map_js_var'];
                                    var names = $layers_names_json;
                                    var layers = [];
                                    try {
                                        map.eachLayer(function(l){
                                            try { if (l && l.options && l.options.name && names.indexOf(l.options.name) !== -1) { layers.push(l); } } catch(e){}
                                        });
                                    } catch(e){}
                                    var s = document.getElementById('rv_op_slider');
                                    var lbl = document.getElementById('rv_op_val');
                                    function setAll(v){
                                        var op = (parseInt(v)||60)/100.0;
                                        try { layers.forEach(function(l){ if (l && l.setOpacity) l.setOpacity(op); }); } catch(e){}
                                        if (lbl) lbl.textContent = (parseInt(v)||60) + '%';
                                        try { localStorage.setItem('rv_opacity', String(parseInt(v)||60)); } catch(e){}
                                    }
                                    if (s){
                                        try { var saved = parseInt(localStorage.getItem('rv_opacity')); if (!isNaN(saved)) { s.value = String(saved); setAll(saved); } } catch(e){}
                                        s.addEventListener('input', function(){ setAll(s.value); });
                                    }
                                } catch (e) { /* ignore */ }
                            })();
                        </script>
                    """)
                op_html = _tmpl.safe_substitute(
                    map_js_var=map_js_var, layers_names_json=layers_names_json, init_pct=init_pct
                )
                m.get_root().add_child(folium.Element(op_html))
        except Exception:
            pass
except Exception:
    pass

# Satellite overlays
with SB.expander("Satellite"):
    if not st.session_state.get("reduce_flash", True):
    with SB.form("sat_form"):
            sat_true = st.checkbox(
                "GOES-East Truecolor", value=qp_sat not in ("0", "false", "no") if qp_sat is not None else False
            )
            sat_ir = st.checkbox(
                "GOES-East IR", value=qp_sati not in ("0", "false", "no") if qp_sati is not None else False
            )
            sat_op = st.slider(testid("sat_opacity") + "Opacity", min_value=10, max_value=100, value=60, step=5, key="sat_opacity")
            st.caption("Tip: use the in-map Satellite Opacity slider (top-right) for smooth fades without reruns.")
            st.form_submit_button(testid("sat_apply") + "Apply satellite")
    else:
        # Read initial state from URL seeds; toggle in-map to avoid reruns
        sat_true = qp_sat not in ("0", "false", "no") if qp_sat is not None else False
        sat_ir = qp_sati not in ("0", "false", "no") if qp_sati is not None else False
        sat_op = int(st.session_state.get("sat_opacity", 60))
        st.metric("Satellite opacity", f"{int(sat_op)}%")
        st.caption("Toggle Truecolor/IR in the Layers control to prevent full reruns.")
    sat_layer_vars: list[str] = []
    # Always register satellite layers so the Layers control can toggle them
    try:
        _sat_true_layer = folium.TileLayer(
            tiles="https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-truecolor/{z}/{x}/{y}.jpg",
            attr="GOES-East Truecolor (IEM)",
            name="Satellite (Truecolor)",
            overlay=True,
            control=True,
            show=bool(sat_true),
            opacity=float(sat_op) / 100.0,
        )
        _sat_true_layer.add_to(m)
        try:
            sat_layer_vars.append(_sat_true_layer.get_name())
        except Exception:
            pass
    except Exception:
        st.warning("Truecolor tile unavailable.")
    try:
        _sat_ir_layer = folium.TileLayer(
            tiles="https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-ir/{z}/{x}/{y}.jpg",
            attr="GOES-East IR (IEM)",
            name="Satellite (IR)",
            overlay=True,
            control=True,
            show=bool(sat_ir),
            opacity=float(sat_op) / 100.0,
        )
        _sat_ir_layer.add_to(m)
        try:
            sat_layer_vars.append(_sat_ir_layer.get_name())
        except Exception:
            pass
    except Exception:
        st.warning("IR tile unavailable.")
    # In-map Satellite opacity control (client-side only)
    if not st.session_state.get("opacity_drawer", True):
        try:
            if sat_layer_vars:
                try:
                    map_js_var = m.get_name()
                except Exception:
                    map_js_var = "map"
                layers_js = ", ".join(sat_layer_vars)
                init_pct = int(st.session_state.get("sat_opacity", 60))
                _sat_tmpl = Template("""
                    <div id='sat_op_wrap' style='position: fixed; top: 120px; right: 10px; z-index: 9999; background: rgba(255,255,255,0.95); padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'>
                        <div style='font-weight:600; margin-bottom:4px;'>Satellite Opacity</div>
                        <input id='sat_op_slider' type='range' min='10' max='100' step='5' value='$init_pct' style='width:140px;'>
                        <span id='sat_op_val' style='margin-left:6px;'>$init_pct%</span>
                        <button id='sat_op_close' title='Hide' style='margin-left:8px; padding:0 6px;'>×</button>
                    </div>
                    <script>
                        (function(){
                            try {
                                var map = window['$map_js_var'];
                                var layers = [$layers_js];
                                var s = document.getElementById('sat_op_slider');
                                var lbl = document.getElementById('sat_op_val');
                                var closeBtn = document.getElementById('sat_op_close');
                                function setAll(v){
                                    var op = (parseInt(v)||60)/100.0;
                                    try { layers.forEach(function(l){ if (l && l.setOpacity) l.setOpacity(op); }); } catch(e){}
                                    if (lbl) lbl.textContent = (parseInt(v)||60) + '%';
                                    try { localStorage.setItem('sat_opacity', String(parseInt(v)||60)); } catch(e){}
                                }
                                if (s){
                                    try { var saved = parseInt(localStorage.getItem('sat_opacity')); if (!isNaN(saved)) { s.value = String(saved); setAll(saved); } } catch(e){}
                                    s.addEventListener('input', function(){ setAll(s.value); });
                                }
                                if (closeBtn){ closeBtn.addEventListener('click', function(){ var w = document.getElementById('sat_op_wrap'); if (w) w.style.display = 'none'; }); }
                            } catch (e) { /* ignore */ }
                        })();
                    </script>
                """)
                sat_op_html = _sat_tmpl.safe_substitute(map_js_var=map_js_var, layers_js=layers_js, init_pct=init_pct)
                m.get_root().add_child(folium.Element(sat_op_html))
        except Exception:
            pass

# Lightning (GOES GLM Flash Extent Density)
with SB.expander("Lightning (GLM)"):
    if not st.session_state.get("reduce_flash", True):
    with SB.form("glm_form"):
            glm_on = st.checkbox(
                "Show GLM Flash Extent Density",
                value=qp_glm not in ("0", "false", "no") if qp_glm is not None else False,
            )
            glm_op = st.slider(testid("glm_opacity") + "Opacity", min_value=10, max_value=100, value=60, step=5, key="glm_opacity")
            st.caption("Tip: use the in-map GLM Opacity slider (top-right) for smooth fades without reruns.")
            st.form_submit_button(testid("glm_apply") + "Apply GLM")
    else:
        glm_on = qp_glm not in ("0", "false", "no") if qp_glm is not None else False
        glm_op = int(st.session_state.get("glm_opacity", 60))
        st.metric("GLM opacity", f"{int(glm_op)}%")
        st.caption("Toggle GLM in the Layers control to prevent full reruns.")
    glm_layer_vars: list[str] = []
    # Always register GLM layer so the Layers control can toggle it
    try:
        _glm_layer = folium.TileLayer(
            tiles="https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-glm/{z}/{x}/{y}.png",
            attr="GOES-East GLM (IEM)",
            name="Lightning (GLM)",
            overlay=True,
            control=True,
            show=bool(glm_on),
            opacity=float(glm_op) / 100.0,
        )
        _glm_layer.add_to(m)
        try:
            glm_layer_vars.append(_glm_layer.get_name())
        except Exception:
            pass
    except Exception:
        st.warning("GLM tile unavailable.")
    # In-map GLM opacity control (client-side only)
    if not st.session_state.get("opacity_drawer", True):
        try:
            if glm_layer_vars:
                try:
                    map_js_var = m.get_name()
                except Exception:
                    map_js_var = "map"
                layers_js = ", ".join(glm_layer_vars)
                init_pct = int(st.session_state.get("glm_opacity", 60))
                _glm_tmpl = Template("""
                    <div id='glm_op_wrap' style='position: fixed; top: 170px; right: 10px; z-index: 9999; background: rgba(255,255,255,0.95); padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'>
                        <div style='font-weight:600; margin-bottom:4px;'>GLM Opacity</div>
                        <input id='glm_op_slider' type='range' min='10' max='100' step='5' value='$init_pct' style='width:140px;'>
                        <span id='glm_op_val' style='margin-left:6px;'>$init_pct%</span>
                        <button id='glm_op_close' title='Hide' style='margin-left:8px; padding:0 6px;'>×</button>
                    </div>
                    <script>
                        (function(){
                            try {
                                var map = window['$map_js_var'];
                                var layers = [$layers_js];
                                var s = document.getElementById('glm_op_slider');
                                var lbl = document.getElementById('glm_op_val');
                                var closeBtn = document.getElementById('glm_op_close');
                                function setAll(v){
                                    var op = (parseInt(v)||60)/100.0;
                                    try { layers.forEach(function(l){ if (l && l.setOpacity) l.setOpacity(op); }); } catch(e){}
                                    if (lbl) lbl.textContent = (parseInt(v)||60) + '%';
                                    try { localStorage.setItem('glm_opacity', String(parseInt(v)||60)); } catch(e){}
                                }
                                if (s){
                                    try { var saved = parseInt(localStorage.getItem('glm_opacity')); if (!isNaN(saved)) { s.value = String(saved); setAll(saved); } } catch(e){}
                                    s.addEventListener('input', function(){ setAll(s.value); });
                                }
                                if (closeBtn){ closeBtn.addEventListener('click', function(){ var w = document.getElementById('glm_op_wrap'); if (w) w.style.display = 'none'; }); }
                            } catch (e) { /* ignore */ }
                        })();
                    </script>
                """)
                glm_op_html = _glm_tmpl.safe_substitute(map_js_var=map_js_var, layers_js=layers_js, init_pct=init_pct)
                m.get_root().add_child(folium.Element(glm_op_html))
        except Exception:
            pass

# Consolidated Layer Opacity drawer (Radar/Satellite/GLM)
try:
    # Collect current layer var lists from locals
    _rv_vars = []
    try:
        _rv_vars = radar_layer_vars if "radar_layer_vars" in locals() else []
    except Exception:
        _rv_vars = []
    _sat_vars = []
    try:
        _sat_vars = sat_layer_vars if "sat_layer_vars" in locals() else []
    except Exception:
        _sat_vars = []
    _glm_vars = []
    try:
        _glm_vars = glm_layer_vars if "glm_layer_vars" in locals() else []
    except Exception:
        _glm_vars = []

    if st.session_state.get("opacity_drawer", True) and (_rv_vars or _sat_vars or _glm_vars):
        # Determine map variable name used by Leaflet
        try:
            map_js_var = m.get_name()
        except Exception:
            map_js_var = "map"

        # Pass layer display names into JS and resolve them from the Leaflet map instance
        rv_names = json.dumps(_rv_vars)
        sat_names = json.dumps(_sat_vars)
        glm_names = json.dumps(_glm_vars)
        rv_init = int(st.session_state.get("map_radar_opacity", 60))
        sat_init = int(st.session_state.get("sat_opacity", 60))
        glm_init = int(st.session_state.get("glm_opacity", 60))

        _drawer_tmpl = Template("""
                <div id='op_drawer' style='position: fixed; top: 100px; right: 10px; z-index: 9998; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'>
                    <div style='display:flex; align-items:center; justify-content:space-between;'>
                        <div style='font-weight:700;'>Layer Opacity</div>
                        <button id='op_drawer_close' title='Hide' style='padding:0 6px; font-size:14px;'>×</button>
                    </div>
                    <div style='margin-top:6px;'>
                        <div style='margin:6px 0;'>Radar <input id='op_rv' type='range' min='10' max='100' step='5' value='$rv_init' style='width:140px; margin-left:8px;'><span id='op_rv_val' style='margin-left:6px;'>$rv_init%</span></div>
                        <div style='margin:6px 0;'>Satellite <input id='op_sat' type='range' min='10' max='100' step='5' value='$sat_init' style='width:140px; margin-left:8px;'><span id='op_sat_val' style='margin-left:6px;'>$sat_init%</span></div>
                        <div style='margin:6px 0;'>GLM <input id='op_glm' type='range' min='10' max='100' step='5' value='$glm_init' style='width:140px; margin-left:8px;'><span id='op_glm_val' style='margin-left:6px;'>$glm_init%</span></div>
                    </div>
                </div>
                <button id='op_drawer_open' title='Layer Opacity' style='position: fixed; top: 100px; right: 10px; z-index: 9997; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'>Opacity</button>
                <script>
                    (function(){
                        try {
                            var map = window['$map_js_var'];
                            function findLayers(names){
                                var arr = [];
                                try {
                                    map.eachLayer(function(l){ try { if(l && l.options && l.options.name && names.indexOf(l.options.name)!==-1){ arr.push(l); } } catch(e){} });
                                } catch(e){}
                                return arr;
                            }
                            var rvLayers = findLayers($rv_names);
                            var satLayers = findLayers($sat_names);
                            var glmLayers = findLayers($glm_names);
                            function bind(id, lblId, arr, lsKey, urlParam){
                                var s = document.getElementById(id); var lbl = document.getElementById(lblId);
                                function setAll(v){ var op = (parseInt(v)||60)/100.0; try { (arr||[]).forEach(function(l){ if(l&&l.setOpacity) l.setOpacity(op); }); } catch(e){} if(lbl) lbl.textContent=(parseInt(v)||60)+'%'; try{localStorage.setItem(lsKey,String(parseInt(v)||60));}catch(e){} if(urlParam==='ro'){ try{ var url=new URL(window.location.href); url.searchParams.set('ro',String(parseInt(v)||60)); window.history.replaceState(null,'',url.toString()); }catch(e){} } }
                                if(s){ try{ var saved=parseInt(localStorage.getItem(lsKey)); if(!isNaN(saved)){ s.value=String(saved); setAll(saved); } }catch(e){} s.addEventListener('input', function(){ setAll(s.value); }); }
                            }
                            bind('op_rv','op_rv_val',rvLayers,'rv_opacity','ro');
                            bind('op_sat','op_sat_val',satLayers,'sat_opacity',null);
                            bind('op_glm','op_glm_val',glmLayers,'glm_opacity',null);
                            var closeBtn = document.getElementById('op_drawer_close'); if(closeBtn){ closeBtn.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d) d.style.display='none'; var o=document.getElementById('op_drawer_open'); if(o) o.style.display='inline-block'; }); }
                            var openBtn = document.getElementById('op_drawer_open'); if(openBtn){ openBtn.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d) d.style.display='block'; this.style.display='none'; }); }
                        } catch(e){}
                    })();
                </script>
            """)

        drawer_html = _drawer_tmpl.safe_substitute(
            map_js_var=map_js_var,
            rv_names=rv_names,
            sat_names=sat_names,
            glm_names=glm_names,
            rv_init=rv_init,
            sat_init=sat_init,
            glm_init=glm_init,
        )

        # Hide individual HUDs if drawer is present; we simply avoid rendering them above
        m.get_root().add_child(folium.Element(drawer_html))
except Exception:
    pass

# Small, non-intrusive banner with current URL/port and a copy button
st.markdown(
    """
    <div id="__url_banner" style="position: fixed; bottom: 8px; left: 10px; z-index: 1000; background: white; border: 1px solid #ddd; border-radius: 12px; padding: 6px 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); font-size: 12px; max-width: 70vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
        <span style="margin-right:8px;">🔗 URL:</span>
        <a id="__url_link" href="#" style="text-decoration:none; color:#0366d6;">loading…</a>
        <button id="__copy_btn" style="margin-left:10px; padding:2px 8px; font-size:12px;">Copy</button>
    </div>
    <script>
    (function(){
        try {
            var href = window.location.href;
            var a = document.getElementById('__url_link');
            if (a) { a.textContent = href; a.href = href; }
            var b = document.getElementById('__copy_btn');
            if (b) {
                b.onclick = async function(){
                    try { await navigator.clipboard.writeText(href); b.textContent = 'Copied'; setTimeout(function(){ b.textContent = 'Copy'; }, 1000); } catch (e) {}
                };
            }
        } catch(e) { /* ignore */ }
    })();
    </script>
    """,
    unsafe_allow_html=True,
)

# SPC Outlooks
with SB.expander("SPC Outlooks"):
    _pip = status_pip_html("spc")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px;'>"
        f"<div style='flex:1;'>Show SPC Convective Outlook</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    spc_on = st.checkbox(
        " ", key="spc_on_cb", value=qp_spc not in ("0", "false", "no") if qp_spc is not None else False
    )
    try:
        spc_day = int(qp_spcd) if qp_spcd is not None else 1
    except Exception:
        spc_day = 1
    spc_idx = max(0, min(2, int(spc_day) - 1)) if isinstance(spc_day, int) else 0
    spc_sel = st.selectbox("Day", options=[1, 2, 3], index=spc_idx)
    _spc_day_int = int(spc_sel or 1)
    add_spc_outlooks(m, spc_on, _spc_day_int)
    if spc_on:
        pass

with SB.expander("Earthquakes"):
    _pip = status_pip_html("eq")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px;'>"
        f"<div style='flex:1;'>Show earthquakes (USGS, past 24h)</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    eq_on = st.checkbox(" ", key="eq_on_cb", value=qp_eq not in ("0", "false", "no") if qp_eq is not None else False)
    try:
        eq_seed = float(qp_eqmin) if qp_eqmin is not None else 2.5
    except Exception:
        eq_seed = 2.5
    eq_minmag = st.slider("Min magnitude", 0.0, 6.0, float(eq_seed), 0.1, key="eq_min_mag")
    add_earthquakes(m, eq_on, float(eq_minmag))

with SB.expander("Hurricanes & Tropical Storms"):
    _pip = status_pip_html("trp")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px;'>"
        f"<div style='flex:1;'>Show active tropical systems (NHC)</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    trp_on = st.checkbox(
        " ", key="trp_on_cb", value=qp_trp not in ("0", "false", "no") if qp_trp is not None else False
    )
    add_tropical(m, trp_on)

with SB.expander("Wildfires"):
    _pip = status_pip_html("wf")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px;'>"
        f"<div style='flex:1;'>Show active wildfires</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    wf_on = st.checkbox(" ", key="wf_on_cb", value=qp_wf not in ("0", "false", "no") if qp_wf is not None else False)
    add_wildfires(m, wf_on)
# One layer per event for polygons/markers
layers_by_event: dict[str, folium.FeatureGroup] = {}
bounds: list[tuple[float, float]] = []


def _add_bounds_from_coords(coord_list):
    try:
        for lon, lat in coord_list:
            bounds.append((lat, lon))
    except Exception:
        pass


def _default_center() -> tuple[float, float]:
    try:
        if CENTER_LAT and CENTER_LON:
            return (float(CENTER_LAT), float(CENTER_LON))
    except Exception:
        pass
    return (37.97, -87.57)


# Draw polygons and trigger markers
for f in features:
    geom = f.get("geometry") or {}
    props = f.get("properties") or {}
    event = props.get("event") or "?"
    # Category filter
    sel_cats = st.session_state.get("map_cat_filters")
    if sel_cats is not None:
        if _cat_for_event(str(event)) not in sel_cats:
            continue
    if selected_events and event not in selected_events:
        continue
    sev = props.get("severity") or "?"
    color = SEVERITY_COLOR.get(sev, "#74add1")
    # Compute derived action via rules (if any)
    # Alert age minutes (best effort)
    eff = props.get("effective") or props.get("onset") or props.get("sent") or props.get("published")
    age_min = None
    if eff:
        try:
            # normalize 'Z' to +00:00 for fromisoformat
            from datetime import datetime, timezone

            t = eff.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                from datetime import timezone as _tz

                dt = dt.replace(tzinfo=_tz.utc)
            now = datetime.now(timezone.utc)
            age_min = int(max(0, (now - dt).total_seconds() // 60))
        except Exception:
            age_min = None
    counties = _extract_county_fips(props)
    action = None
    if rules:
        try:
            action = eval_rules(rules, severity=sev, event=event, counties_fips=counties, alert_age_minutes=age_min)
        except Exception:
            action = None
    # Compute trigger bool (filters + FIPS intersection) independent of rules
    base_match = _alert_matches_filters(props)
    intersects_target = bool(set(counties) & set(TARGET_COUNTY_FIPS))
    is_trigger = bool(base_match and intersects_target)
    triggered = (bool(action) and trig_rules) or (bool(is_trigger) and trig_filters)
    if only_triggered and not triggered:
        continue
    action_str = action or "—"
    cap_id = props.get("id") or props.get("capId") or props.get("cap_id")
    deeplink = f"?page=Ads&from=map&event={quote_plus(str(event))}&fips={quote_plus(','.join(counties))}&cap={quote_plus(str(cap_id or ''))}"
    popup = (
        f"<b>{event}</b> &nbsp; <i>(derived: {action_str}{' | TRIGGER' if is_trigger else ''})</i>"
        f"<br/>Severity: {sev}"
        f"<br/>Start: {_fmt_time_short(eff)}"
        f"<br/>CAP: {cap_id}"
        f"<br/>FIPS: {', '.join(counties) if counties else '—'}"
        f"<br/><a href='{deeplink}' target='_top'>Open Ads</a>"
    )
    has_geom = False
    # Ensure an event-specific layer exists
    layer = layers_by_event.get(event)
    if layer is None:
        layer = folium.FeatureGroup(name=event)
        layer.add_to(m)
        layers_by_event[event] = layer
    if geom.get("type") == "Polygon":
        has_geom = True
        coords = geom.get("coordinates", [])
        if coords:
            # Highlight edge if triggered (rules or base trigger)
            edge = "#7a0177" if triggered else color
            if not st.session_state.get("map_fast_mode", True):
                folium.Polygon(
                    [(lat, lon) for lon, lat in coords[0]],
                    color=edge,
                    weight=3 if (action or is_trigger) else 1,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.25,
                    popup=popup,
                ).add_to(layer)
            _add_bounds_from_coords(coords[0])
    elif geom.get("type") == "MultiPolygon":
        has_geom = True
        for poly in geom.get("coordinates", []):
            if not poly:
                continue
            edge = "#7a0177" if triggered else color
            if not st.session_state.get("map_fast_mode", True):
                folium.Polygon(
                    [(lat, lon) for lon, lat in poly[0]],
                    color=edge,
                    weight=3 if (action or is_trigger) else 1,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.25,
                    popup=popup,
                ).add_to(layer)
            _add_bounds_from_coords(poly[0])

    # Add centroid marker to FIPS layer if any targeted counties match
    matched = sorted(set(counties) & set(TARGET_COUNTY_FIPS))
    if matched:
        lat, lon = _first_polygon_centroid(f)
        if lat is not None and lon is not None:
            tip = (
                f"Matched FIPS: {', '.join(matched)}\nAction: {action_str}\nTriggered: {'yes' if is_trigger else 'no'}"
            )
            folium.CircleMarker(
                location=(lat, lon),
                radius=5,
                color="#7a0177" if triggered else "#2c7fb8",
                fill=True,
                fill_opacity=0.9,
                tooltip=tip,
            ).add_to(layer)
            bounds.append((lat, lon))

    # Fallback marker when no geometry but targeted counties or names match
    if not has_geom:
        area_desc = props.get("areaDesc", "")
        name_match = any(name in area_desc for name in TARGET_COUNTIES)
        if intersects_target or name_match:
            lat, lon = _default_center()
            tip = "No polygon from NWS; approximate location\n" + (
                f"FIPS match: {', '.join(matched)}" if matched else f"Area: {area_desc[:80]}"
            )
            folium.Marker(
                location=(lat, lon),
                icon=folium.Icon(color="purple" if (action or is_trigger) else "blue", icon="info-sign"),
                popup=popup,
                tooltip=tip,
            ).add_to(layer)
            bounds.append((lat, lon))

do_zoom = bool(auto_zoom or force_zoom)
if do_zoom and bounds:
    try:
        min_lat = min(b[0] for b in bounds)
        max_lat = max(b[0] for b in bounds)
        min_lon = min(b[1] for b in bounds)
        max_lon = max(b[1] for b in bounds)
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]])
    except Exception:
        pass

# (LayerControl will be added at the end after all overlays are added)

with SB.expander("Historical timeline"):
    _pip = status_pip_html("hist")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:6px;'>"
        f"<div style='flex:1;'>Show historical alerts (timeline)</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    st.checkbox(
        " ", value=st.session_state.get("map_show_hist", False), key="map_show_hist", help="Animate past alerts"
    )
    st.slider(
        "Hours back",
        min_value=1,
        max_value=72,
        value=st.session_state.get("map_hist_hours", 24),
        step=1,
        key="map_hist_hours",
    )
    st.checkbox(
        "Limit to selected events",
        value=st.session_state.get("map_hist_only_selected", True),
        key="map_hist_only_selected",
    )
    st.checkbox(
        "History: filter to triggers",
        value=st.session_state.get("map_hist_only_triggers", False),
        key="map_hist_only_triggers",
    )
    if st.session_state.get("map_show_hist"):
        add_historical_timeline(
            m,
            states,
            int(st.session_state.get("map_hist_hours", 24)),
            bool(st.session_state.get("map_hist_only_selected", True)),
            selected_events,
            bool(st.session_state.get("map_hist_only_triggers", False)),
            _alert_matches_filters,
            _extract_county_fips,
            TARGET_COUNTY_FIPS,
        )

# Storm Reports (LSR) overlay
with SB.expander("Storm Reports (last hours)"):
    _pip = status_pip_html("lsr")
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:8px;'>"
        f"<div style='flex:1;'>Show storm reports (LSR)</div>{_pip}</div>",
        unsafe_allow_html=True,
    )
    show_lsr = st.checkbox(
        " ",
        value=qp_lsr not in ("0", "false", "no") if qp_lsr is not None else False,
        help="Hail, wind, and tornado reports from NWS LSRs",
        key="lsr_on_cb",
    )
    try:
        _hval = int(qp_lsrh) if qp_lsrh is not None else 24
    except Exception:
        _hval = 24
    lsr_hours = st.slider(
        "Hours back", min_value=1, max_value=72, value=max(1, min(72, _hval)), step=1, key="lsr_hours_back"
    )
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        lsr_hail = st.checkbox(
            "Hail", value=qp_lsr_hail not in ("0", "false", "no") if qp_lsr_hail is not None else True
        )
    with lc2:
        lsr_wind = st.checkbox(
            "Wind", value=qp_lsr_wind not in ("0", "false", "no") if qp_lsr_wind is not None else True
        )
    with lc3:
        lsr_tor = st.checkbox(
            "Tornado", value=qp_lsr_tor not in ("0", "false", "no") if qp_lsr_tor is not None else True
        )
    lsr_path = st.checkbox(
        "Approximate tornado paths (connect nearby reports)",
        value=qp_lsr_path not in ("0", "false", "no") if qp_lsr_path is not None else True,
    )
    add_lsr_layers(
        m, states, int(lsr_hours), bool(show_lsr), bool(lsr_hail), bool(lsr_wind), bool(lsr_tor), bool(lsr_path)
    )

# Add LayerControl now that all layers are added
folium.LayerControl(collapsed=False).add_to(m)

# Add a fixed-position severity legend
try:
    legend_items = "".join(
        [
            f"<div style='margin:2px 0;'><span style='display:inline-block;width:10px;height:10px;background:{SEVERITY_COLOR.get(lbl, '#74add1')};margin-right:6px;border:1px solid #999;'></span>{lbl}</div>"
            for lbl in ["Extreme", "Severe", "Moderate", "Minor"]
        ]
    )
    legend_html = f"""
        <div style='position: fixed; bottom: 60px; left: 10px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>
            <div style='font-weight: 600; margin-bottom: 4px;'>Severity</div>
            {legend_items}
        </div>
    """
    m.get_root().add_child(folium.Element(legend_html))
except Exception:
    pass

# Add overlay legends (SPC, GLM, Wildfires) only when enabled
try:
    if "spc_on" in locals() and spc_on:
        spc_items = "".join(
            [
                f"<div style='margin:2px 0;'><span style='display:inline-block;width:10px;height:10px;background:{SPC_COLORS.get(k, '#6baed6')};margin-right:6px;border:1px solid #999;'></span>{k}</div>"
                for k in ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
            ]
        )
        spc_html = f"""
                    <div style='position: fixed; bottom: 60px; left: 140px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>
                        <div style='font-weight:600; margin-bottom:4px;'>SPC</div>
                        {spc_items}
                    </div>
                """
        m.get_root().add_child(folium.Element(spc_html))
except Exception:
    pass

# Show a simple radar reflectivity legend when radar is displayed
try:
    if bool(st.session_state.get("map_radar")) or ("ra_on" in locals() and ra_on):
        radar_legend = """
                    <div style='position: fixed; bottom: 60px; right: 10px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>
                        <div style='font-weight:600; margin-bottom:4px;'>Radar dBZ</div>
                        <div style='display:flex; align-items:center; gap:6px;'>
                            <span style='display:inline-block;width:100px;height:10px;background:linear-gradient(to right, #9ecae1, #3182bd, #08519c, #74c476, #31a354, #006d2c, #ffffb2, #fe9929, #d95f0e, #cc0000, #800026); border:1px solid #999;'></span>
                            <span>5→60+</span>
                        </div>
                    </div>
                """
        m.get_root().add_child(folium.Element(radar_legend))
except Exception:
    pass

try:
    if "glm_on" in locals() and glm_on:
        glm_html = """
                    <div style='position: fixed; bottom: 60px; left: 220px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>
                        <div style='font-weight:600; margin-bottom:4px;'>GLM</div>
                        <div>Higher opacity indicates more flashes</div>
                    </div>
                """
        m.get_root().add_child(folium.Element(glm_html))
except Exception:
    pass

try:
    if "wf_on" in locals() and wf_on:
        wf_html = """
                    <div style='position: fixed; bottom: 60px; left: 300px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>
                        <div style='font-weight:600; margin-bottom:4px;'>Wildfires</div>
                        <div><span style='display:inline-block;width:10px;height:10px;background:#fb6a4a;margin-right:6px;border:1px solid #999;'></span>Perimeter</div>
                    </div>
                """
        m.get_root().add_child(folium.Element(wf_html))
except Exception:
    pass

# Add polished map controls
try:
    plugins.Fullscreen(position="topleft", title="Full screen", title_cancel="Exit").add_to(m)
    plugins.MiniMap(toggle_display=True, minimized=True).add_to(m)
    plugins.MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon", num_digits=4).add_to(m)
except Exception:
    pass

# Add a small radar status label (source, opacity, as-of) when radar is enabled
try:
    if bool(st.session_state.get("map_radar")):
        src = st.session_state.get("map_radar_source", "iem")
        src_name = "IEM NEXRAD" if src == "iem" else "RainViewer"
        opct = int(st.session_state.get("map_radar_opacity", 60))
        asof = datetime.now().astimezone().strftime("%m/%d %H:%M")
        radar_html = f"""
            <div style='position: fixed; top: 70px; left: 10px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); font-size: 12px;'>
                <div style='font-weight: 600;'>Radar</div>
                <div>Source: {src_name}</div>
                <div>Opacity: {opct}%</div>
                <div>As of: {asof}</div>
            </div>
        """
        m.get_root().add_child(folium.Element(radar_html))
except Exception:
    pass

# Update query params to reflect current state; omit defaults to keep URLs short
try:
    qp_out = {}
    _ev = ",".join(st.session_state.get("map_events", unique_events)) or ",".join(unique_events)
    if _ev != ",".join(unique_events):
        qp_out["ev"] = _ev
    if not st.session_state.get("map_only_triggered"):
        qp_out["tg"] = "0"
    if not st.session_state.get("map_trig_rules", True):
        qp_out["tr"] = "0"
    if not st.session_state.get("map_trig_filters", True):
        qp_out["tf"] = "0"
    if not st.session_state.get("map_auto_zoom"):
        qp_out["az"] = "0"
    # Persist Fast mode when turned off
    if not st.session_state.get("map_fast_mode", True):
        qp_out["fm"] = "0"
    if st.session_state.get("map_radar"):
        qp_out["rd"] = "1"
        if (st.session_state.get("map_radar_source") or "iem") != "iem":
            qp_out["rs"] = st.session_state.get("map_radar_source") or "iem"
        _ro = str(int(st.session_state.get("map_radar_opacity", 60)))
        if _ro != "60":
            qp_out["ro"] = _ro
    # Hide live radar while archive active (persist only when turned off)
    if ("ra_on" in locals() and ra_on) and not bool(st.session_state.get("ra_hide_live", True)):
        qp_out["rah"] = "0"
    if st.session_state.get("map_show_hist"):
        qp_out["ht"] = "1"
        _hh = str(int(st.session_state.get("map_hist_hours", 24)))
        if _hh != "24":
            qp_out["hh"] = _hh
        if not st.session_state.get("map_hist_only_selected", True):
            qp_out["hs"] = "0"
        if st.session_state.get("map_hist_only_triggers", False):
            qp_out["hr"] = "1"
    # New overlays
    if "ra_on" in locals() and ra_on:
        qp_out["ra"] = "1"
        # Only include a fixed frame when timeline is disabled
        if not bool(st.session_state.get("ra_timeline_on", True)):
            # Persist that the timeline is disabled
            qp_out["rtl"] = "0"
            try:
                _ram = int(ra_minutes)
            except Exception:
                _ram = 0
            if _ram != 30:
                qp_out["ram"] = str(_ram)
        else:
            # Include timeline flags when non-default
            sp = float(st.session_state.get("ra_speed", 1.0))
            if abs(sp - 1.0) > 1e-6:
                qp_out["rts"] = str(round(sp, 2))
            if not bool(st.session_state.get("ra_loop", True)):
                qp_out["rll"] = "0"
    # Basemap and categories
    _base = st.session_state.get("map_basemap", "Light")
    if _base != "Light":
        qp_out["base"] = _base
    # Selected states
    _sts = st.session_state.get("states_sel", ALL_STATES)
    if _sts != ALL_STATES:
        qp_out["st"] = ",".join(_sts)
    _cats = list(st.session_state.get("map_cat_filters", []))
    short = {
        "Severe": "severe",
        "Flood": "flood",
        "Tropical": "tropical",
        "Winter": "winter",
        "Marine": "marine",
        "Other": "other",
    }
    if _cats and set(_cats) != {"Severe", "Flood", "Tropical", "Winter", "Marine", "Other"}:
        qp_out["cat"] = ",".join(sorted([short.get(c, str(c)) or str(c) for c in _cats]))
    if "sat_true" in locals() and sat_true:
        qp_out["sat"] = "1"
    if "sat_ir" in locals() and sat_ir:
        qp_out["sati"] = "1"
    if "glm_on" in locals() and glm_on:
        qp_out["glm"] = "1"
    if "spc_on" in locals() and spc_on:
        qp_out["spc"] = "1"
        _sd = int(_spc_day_int) if "_spc_day_int" in locals() else 1
        if _sd != 1:
            qp_out["spcd"] = str(_sd)
    # Specialty overlays
    if "eq_on" in locals() and eq_on:
        qp_out["eq"] = "1"
        try:
            _mn = float(eq_minmag)
        except Exception:
            _mn = 2.5
        if _mn != 2.5:
            qp_out["eqmin"] = str(_mn)
    if "trp_on" in locals() and trp_on:
        qp_out["trp"] = "1"
    if "wf_on" in locals() and wf_on:
        qp_out["wf"] = "1"
    if qp_out:
        # Avoid redundant URL updates across reruns: only push when changed,
        # and throttle updates during rapid interactions (e.g., scrubbing).
        try:
            now = time.time()
            qp_hash = "|".join(f"{k}={v}" for k, v in sorted(qp_out.items()))
            last_hash = st.session_state.get("qp_last_hash")
            last_ts = float(st.session_state.get("qp_last_update_ts", 0.0) or 0.0)
            throttle_s = 0.75
            if qp_hash != last_hash and (now - last_ts) >= throttle_s:
                st.query_params.update(qp_out)
                st.session_state["qp_last_hash"] = qp_hash
                st.session_state["qp_last_update_ts"] = now
        except Exception:
            # Fallback to best-effort update on any error
            st.query_params.update(qp_out)
except Exception:
    pass

# Render the map
out = st_folium(m, width=1200, height=700, key="map_embed")
try:
    c = (out or {}).get("center") or {}
    z = (out or {}).get("zoom")
    if isinstance(c, dict) and "lat" in c and "lng" in c and z is not None:
        clat = float(c.get("lat"))
        clon = float(c.get("lng"))
        cz = int(z)
        lat4 = f"{clat:.4f}"
        lon4 = f"{clon:.4f}"
        zstr = str(cz)
        if (lat4 != str(qp_lat)) or (lon4 != str(qp_lon)) or (zstr != str(qp_z)):
            try:
                _upd = {"lat": lat4, "lon": lon4, "z": zstr}
                upd_hash = "|".join(f"{k}={v}" for k, v in sorted(_upd.items()))
                last_hash = st.session_state.get("qp_last_hash_center")
                last_ts = float(st.session_state.get("qp_center_last_update_ts", 0.0) or 0.0)
                throttle_s = 0.75
                now = time.time()
                if upd_hash != last_hash and (now - last_ts) >= throttle_s:
                    st.query_params.update(_upd)
                    st.session_state["qp_last_hash_center"] = upd_hash
                    st.session_state["qp_center_last_update_ts"] = now
            except Exception:
                pass
except Exception:
    pass

# Persist current radar preferences to localStorage (non-blocking)
try:
    _rd = "1" if bool(st.session_state.get("map_radar")) else "0"
    _rs = st.session_state.get("map_radar_source", "iem")
    _ro = str(int(st.session_state.get("map_radar_opacity", 60)))
    _rah = "1" if bool(st.session_state.get("ra_hide_live", True)) else "0"
    st.markdown(
        """
        <script>
        (function() {{
            try {{
                localStorage.setItem('radar_on','{rd}');
                localStorage.setItem('radar_source','{rs}');
                localStorage.setItem('rv_opacity','{ro}');
                localStorage.setItem('ra_hide_live','{rah}');
                // Satellite
                try {{ localStorage.setItem('sat_true','{sat_true}'); }} catch(e){{}}
                try {{ localStorage.setItem('sat_ir','{sat_ir}'); }} catch(e){{}}
                try {{ localStorage.setItem('sat_opacity','{sat_op}'); }} catch(e){{}}
                // GLM
                try {{ localStorage.setItem('glm_on','{glm_on}'); }} catch(e){{}}
                try {{ localStorage.setItem('glm_opacity','{glm_op}'); }} catch(e){{}}
                // SPC
                try {{ localStorage.setItem('spc_on','{spc_on}'); }} catch(e){{}}
                try {{ localStorage.setItem('spc_day','{spc_day}'); }} catch(e){{}}
                // Basemap
                try {{ localStorage.setItem('basemap','{basemap}'); }} catch(e){{}}
                // Categories (short tokens CSV)
                try {{ localStorage.setItem('cat_filters','{cat_filters}'); }} catch(e){{}}
                // States CSV
                try {{ localStorage.setItem('states','{states}'); }} catch(e){{}}
                // Specialty overlays
                try {{ localStorage.setItem('eq_on','{eq_on}'); }} catch(e){{}}
                try {{ localStorage.setItem('eq_minmag','{eq_minmag}'); }} catch(e){{}}
                try {{ localStorage.setItem('trp_on','{trp_on}'); }} catch(e){{}}
                try {{ localStorage.setItem('wf_on','{wf_on}'); }} catch(e){{}}
            }} catch(e) {{ /* ignore */ }}
        }})();
        </script>
        """.format(
            rd=_rd,
            rs=_rs,
            ro=_ro,
            rah=_rah,
            sat_true=("1" if bool(locals().get("sat_true")) else "0"),
            sat_ir=("1" if bool(locals().get("sat_ir")) else "0"),
            sat_op=str(int(st.session_state.get("sat_opacity", 60))),
            glm_on=("1" if bool(locals().get("glm_on")) else "0"),
            glm_op=str(int(st.session_state.get("glm_opacity", 60))),
            spc_on=("1" if bool(locals().get("spc_on")) else "0"),
            spc_day=str(int(locals().get("_spc_day_int", 1))),
            basemap=str(st.session_state.get("map_basemap", "Light")),
            cat_filters=",".join(
                sorted(
                    [
                        {
                            "Severe": "severe",
                            "Flood": "flood",
                            "Tropical": "tropical",
                            "Winter": "winter",
                            "Marine": "marine",
                            "Other": "other",
                        }.get(c, str(c))
                        for c in list(st.session_state.get("map_cat_filters", []))
                    ]
                )
            )
            if st.session_state.get("map_cat_filters")
            else "",
            states=",".join(st.session_state.get("states_sel", [])),
            eq_on=("1" if bool(locals().get("eq_on")) else "0"),
            eq_minmag=str(st.session_state.get("eq_min_mag", "")),
            trp_on=("1" if bool(locals().get("trp_on")) else "0"),
            wf_on=("1" if bool(locals().get("wf_on")) else "0"),
        ),
        unsafe_allow_html=True,
    )
except Exception:
    pass


# Auto-refresh (JS fallback)
def _effective_refresh(sec: int) -> int:
    try:
        sec = int(sec)
    except Exception:
        sec = 60
    # If fast mode is on and multiple heavy overlays enabled, slow down a bit
    heavy = 0
    heavy += 1 if bool(st.session_state.get("map_radar")) else 0
    heavy += 1 if "sat_true" in locals() and sat_true else 0
    heavy += 1 if "sat_ir" in locals() and sat_ir else 0
    heavy += 1 if "glm_on" in locals() and glm_on else 0
    if bool(st.session_state.get("map_fast_mode", True)) and heavy >= 2:
        return max(sec, 90)
    return sec


if (
    auto_refresh
    and refresh_sec
    and not (("ra_on" in locals() and ra_on) and st.session_state.get("ra_timeline_on", False))
):
    try:
        st.markdown(
            f"""
            <script>
            setTimeout(function() {{ window.location.reload(); }}, {_effective_refresh(int(refresh_sec)) * 1000});
            </script>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass

# Export
if st.button("Export GeoJSON"):
    gj = {"type": "FeatureCollection", "features": features}
    st.download_button("Download", data=json.dumps(gj), file_name="alerts.geojson", mime="application/geo+json")

# Shareable link controls
try:
    _events = ",".join(st.session_state.get("map_events", unique_events)) or ",".join(unique_events)
    _trig = "1" if st.session_state.get("map_only_triggered") else "0"
    _autoz = "1" if st.session_state.get("map_auto_zoom") else "0"
    _radar = "1" if st.session_state.get("map_radar") else "0"
    _tr = "1" if st.session_state.get("map_trig_rules", True) else "0"
    _tf = "1" if st.session_state.get("map_trig_filters", True) else "0"
    _hist = "1" if st.session_state.get("map_show_hist") else "0"
    _hh = str(int(st.session_state.get("map_hist_hours", 24)))
    _hsel = "1" if st.session_state.get("map_hist_only_selected", True) else "0"
    _htr = "1" if st.session_state.get("map_hist_only_triggers", False) else "0"
    # LSR states (read current controls)
    _lsr = "1" if show_lsr else "0"
    _lsrh = str(int(lsr_hours)) if show_lsr else None
    _lsrhail = "1" if (show_lsr and lsr_hail) else "0"
    _lsrwind = "1" if (show_lsr and lsr_wind) else "0"
    _lsrtor = "1" if (show_lsr and lsr_tor) else "0"
    _lsrpath = "1" if (show_lsr and lsr_path) else "0"

    parts = ["?page=Map"]
    # Selected states (omit when default)
    _sts = st.session_state.get("states_sel", ALL_STATES)
    if _sts != ALL_STATES:
        parts.append(f"st={quote_plus(','.join(_sts))}")
    # Basemap and categories
    _base = st.session_state.get("map_basemap", "Light")
    if _base != "Light":
        parts.append(f"base={quote_plus(str(_base))}")
    _cats = list(st.session_state.get("map_cat_filters", []))
    short = {
        "Severe": "severe",
        "Flood": "flood",
        "Tropical": "tropical",
        "Winter": "winter",
        "Marine": "marine",
        "Other": "other",
    }
    if _cats and set(_cats) != {"Severe", "Flood", "Tropical", "Winter", "Marine", "Other"}:
        parts.append(f"cat={quote_plus(','.join(sorted([short.get(c, str(c)) or str(c) for c in _cats])))}")
    if _events != ",".join(unique_events):
        parts.append(f"ev={quote_plus(str(_events))}")
    if _trig != "1":
        parts.append(f"tg={_trig}")
    if _tr != "1":
        parts.append(f"tr={_tr}")
    if _tf != "1":
        parts.append(f"tf={_tf}")
    if _autoz != "1":
        parts.append(f"az={_autoz}")
    if not st.session_state.get("map_fast_mode", True):
        parts.append("fm=0")
    if _radar == "1":
        parts.append("rd=1")
        _rs = st.session_state.get("map_radar_source", "iem")
        if _rs != "iem":
            parts.append(f"rs={quote_plus(str(_rs))}")
        _ro = str(int(st.session_state.get("map_radar_opacity", 60)))
        if _ro != "60":
            parts.append(f"ro={_ro}")
        if ("ra_on" in locals() and ra_on) and not bool(st.session_state.get("ra_hide_live", True)):
            parts.append("rah=0")
    if _hist == "1":
        parts.append("ht=1")
        if _hh != "24":
            parts.append(f"hh={_hh}")
        if _hsel != "1":
            parts.append(f"hs={_hsel}")
        if _htr == "1":
            parts.append("hr=1")
    # LSR share params (omit when defaults/hidden)
    if _lsr == "1":
        parts.append("lsr=1")
        if _lsrh and _lsrh != "24":
            parts.append(f"lsrh={_lsrh}")
        if _lsrhail != "1":
            parts.append(f"lsrhail={_lsrhail}")
        if _lsrwind != "1":
            parts.append(f"lsrwind={_lsrwind}")
        if _lsrtor != "1":
            parts.append(f"lsrtor={_lsrtor}")
        if _lsrpath != "1":
            parts.append(f"lsrpath={_lsrpath}")
    # New overlays to share link
    if "ra_on" in locals() and ra_on:
        parts.append("ra=1")
        # Only include a fixed frame when timeline is disabled
        if not bool(st.session_state.get("ra_timeline_on", True)):
            parts.append("rtl=0")
            try:
                _ram = int(ra_minutes)
            except Exception:
                _ram = 0
            if _ram != 30:
                parts.append(f"ram={_ram}")
        else:
            # Timeline flags
            sp = float(st.session_state.get("ra_speed", 1.0))
            if abs(sp - 1.0) > 1e-6:
                parts.append(f"rts={round(sp, 2)}")
            if not bool(st.session_state.get("ra_loop", True)):
                parts.append("rll=0")
    if "sat_true" in locals() and sat_true:
        parts.append("sat=1")
    if "sat_ir" in locals() and sat_ir:
        parts.append("sati=1")
    if "glm_on" in locals() and glm_on:
        parts.append("glm=1")
    if "spc_on" in locals() and spc_on:
        parts.append("spc=1")
        _sd = int(_spc_day_int) if "_spc_day_int" in locals() else 1
        if _sd != 1:
            parts.append(f"spcd={_sd}")
    if "eq_on" in locals() and eq_on:
        parts.append("eq=1")
        try:
            _mn = float(eq_minmag)
        except Exception:
            _mn = 2.5
        if _mn != 2.5:
            parts.append(f"eqmin={_mn}")
    if "trp_on" in locals() and trp_on:
        parts.append("trp=1")
    if "wf_on" in locals() and wf_on:
        parts.append("wf=1")
    rel_link = "&".join(parts)
    st.caption("Share this view")
    show_map_link = st.checkbox("Show link", value=False, key="map_show_link")
    if show_map_link:
        st.code(rel_link)
        st.caption("Tip: use the copy icon on the code box to copy the link.")
    st.link_button("Open share link", rel_link, type="secondary")
except Exception:
    pass
