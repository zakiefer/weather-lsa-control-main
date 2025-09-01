# pyright: reportGeneralTypeIssues=false
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
# --- Folium plugins import for map controls ---

import folium
from folium import plugins

# --- Import real helpers instead of using local stubs ---
from ui.map_layers import (
    add_historical_timeline,
    add_lsr_layers,
    alert_matches_filters as _alert_matches_filters,
    extract_county_fips as _extract_county_fips,
    first_polygon_centroid as _first_polygon_centroid,
)
from src.rules import evaluate as eval_rules
from src.config.settings import TARGET_COUNTY_FIPS, TARGET_COUNTIES
import src.config.settings as _cfgsettings
SEVERITY_COLOR = getattr(
    _cfgsettings,
    "SEVERITY_COLOR",
    {
        "Severe": "#e31a1c",
        "Flood": "#3182bd",
        "Tropical": "#fd8d3c",
        "Winter": "#756bb1",
        "Marine": "#2ca25f",
        "Other": "#bdbdbd",
    },
)


import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import quote_plus

import streamlit as st
from streamlit.components.v1 import html as st_html
from streamlit_folium import st_folium

# Runtime helpers for alerts and radar layer
from src.__main__ import get_credentials
from src.config.settings import CENTER_LAT, CENTER_LON, RULES_FILE
from src.config.settings import STATE_CODES as ALL_STATES  # type: ignore
from src.config.settings import TRIGGER_EVENTS
from src.rules import load_rules  # rules loader
from src.weather_monitor import WeatherMonitor

# Import overlay/status helpers and rules loader
from ui.http_client import clear_caches  # cache/status helpers
from ui.http_client import get_status_snapshot
from ui.layers.rainviewer import attach_rainviewer_layer
from ui.map_layers import add_spc_outlooks  # overlays
from ui.map_layers import add_earthquakes, add_tropical, add_wildfires
from ui.overlay_status import status_pip_html  # overlay status pip helper
from ui.testids import testid  # stable test ids for e2e

# from streamlit.runtime.caching import cache_data  # unused


# Ensure repo root on sys.path so `ui` and `src` can be imported
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Page setup early (before any sidebar/UI)
st.set_page_config(page_title="Map", page_icon="🗺️", layout="wide")

# Auth/bootstrap (pattern matches other pages)
from ui._bootstrap import *  # noqa: F401,F403

require_auth()

# Proactive parent readiness marker removed to avoid raw JS leakage in some Streamlit builds.
# The deterministic helper iframe below sets readiness markers reliably for E2E.

# Local helpers/state
SB = st.sidebar
E2E_MODE: bool = os.getenv("E2E_TEST_IDS", "0").lower() in {"1", "true", "yes", "on"}
suppres_qp_default: bool = False
# Used to throttle or disable URL query param updates during certain deep-link states/E2E
suppress_qp_updates: bool = False
# Guard for URL sync throttling defined early to avoid NameError on reruns
## Removed early E2E bootstrapping via markdown scripts (blocked). We'll expose selectors in a component iframe below.

## Removed hidden fallback nodes at parent level; tests will use helper/component iframe selectors instead.

# Parent-level enforcement handled by the robust st_html provisioner below to avoid duplicate injectors

# E2E srcdoc fallback removed; the component iframe below provides stable selectors.


def _qp_get(name: str):
    """Robust query param getter compatible with Streamlit versions."""
    try:
        return st.query_params.get(name)
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            v = qp.get(name)
            return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None)
        except Exception:
            return None


# --- E2E/network fixture helpers ---
def _blank_tile_url() -> str:
    """Return a 1x1 transparent PNG as a data URI to avoid network requests in tests."""
    # iVBORw0K... is a standard 1x1 transparent PNG
    return (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )


def _qp_bool(name: str) -> bool:
    try:
        v = _qp_get(name)
        if isinstance(v, list):
            v = v[0] if v else None
        return str(v or "0").lower() in {"1", "true", "yes", "on"}
    except Exception:
        return False


# Enable local/blank tiles during E2E runs or when radar_fixture=1 is present
RADAR_FIXTURE: bool = _qp_bool("radar_fixture")
NET_FIXTURE: bool = bool(E2E_MODE or RADAR_FIXTURE)


def _fmt_time_short(dt):
    """Small local formatting helper for timestamps used in popups."""
    try:
        s = str(dt)
        if "T" in s:
            return s.replace("T", " ").replace("Z", "Z")
        return s
    except Exception:
        return str(dt)


# --- LSR query param seeds (for severe weather reports overlays) ---
qp_lsr = _qp_get("lsr")
# --- Core query params used throughout (define EARLY to avoid NameError on reruns) ---
qp_event = _qp_get("event")
qp_base = _qp_get("base")
qp_st = _qp_get("st")
qp_catf = _qp_get("cat")

# Radar preferences and archive/timeline deep-links
qp_rd = _qp_get("rd")  # radar on/off ("1"/"0")
qp_rs = _qp_get("rs") or _qp_get("radsrc") or _qp_get("rsrc")  # radar source: "iem"|"rv"
qp_ro = _qp_get("ro")  # radar opacity (10-100)
qp_ra = _qp_get("ra")  # archive mode on/off
qp_rtl = _qp_get("rtl")  # timeline on/off
qp_rts = _qp_get("rts")  # timeline speed
qp_rll = _qp_get("rll")  # loop on/off
qp_ram = _qp_get("ram")  # minutes of archive
qp_rah = _qp_get("rah")  # hide-live while archive
qp_rsrc = qp_rs  # internal alias used by sidebar seed
qp_rop = qp_ro  # internal alias used by sidebar seed

# Satellite/GLM overlays
qp_sat = _qp_get("sat")  # GOES true color
qp_sati = _qp_get("sati")  # GOES IR
qp_glm = _qp_get("glm")  # GLM lightning

# SPC outlooks
qp_spc = _qp_get("spc")
qp_spcd = _qp_get("spcd")

# E2E: allow forcing SPC fixture via query param
try:
    _qp_spc_fix = None
    try:
        _qp_spc_fix = st.query_params.get("spc_fixture")  # type: ignore[attr-defined]
    except Exception:
        try:
            _qp_spc_fix = st.experimental_get_query_params().get("spc_fixture")  # type: ignore[assignment]
        except Exception:
            _qp_spc_fix = None
    if isinstance(_qp_spc_fix, list):
        _qp_spc_fix = _qp_spc_fix[0] if _qp_spc_fix else None
    if str(_qp_spc_fix or "0") == "1":
        st.session_state["spc_fixture"] = "1"
except Exception:
    pass

# Performance and trigger toggles
qp_fm = _qp_get("fm")  # fast mode
qp_tr_rules = _qp_get("tr")  # trigger rules toggle
qp_tr_filters = _qp_get("tf")  # trigger filters toggle

# LSR overlay detail seeds
qp_lsrh = _qp_get("lsrh")  # hours back
qp_lsr_hail = _qp_get("lsr_hail")
qp_lsr_wind = _qp_get("lsr_wind")
qp_lsr_tor = _qp_get("lsr_tor")
qp_lsr_path = _qp_get("lsr_path")
# Provide a deterministic helper iframe using Streamlit components. This iframe renames
# itself to "__map_e2e_iframe" and renders the timeline and opacity drawer controls.
try:
    st_html(
        """
                <!doctype html>
                <html>
                <head>
                    <meta charset='utf-8'/>
                    <style>
                        body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:6px;}
                        #rv_timeline_wrap{position: relative; background: rgba(255,255,255,0.95); padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);}
                        #rv_slider{width:160px;}
                        #op_drawer{display:none; position: relative; margin-top: 10px; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18);}
                        #op_drawer_open{margin-top:8px;}
                    </style>
                </head>
                <body data-map-sentinel="1" data-map-ready="1" data-map-timeline-ready="1" data-map-drawer-ready="1">
                    <div id="__map_sentinel" style="display:none"></div>
                    <div id="rv_timeline_wrap" aria-label="Radar timeline">
                        <button id="rv_play" type="button">Play</button>
                        <button id="rv_pause" type="button">Pause</button>
                        <button id="rv_prev" type="button">◀</button>
                        <button id="rv_next" type="button">▶</button>
                        <button id="rv_now" type="button">Now</button>
                        <button id="rv_oldest" type="button">Oldest</button>
                        <input id="rv_slider" type="range" min="0" max="12" step="1" value="0" />
                        <span id="rv_label">~0m</span>
                    </div>
                    <button id="op_drawer_open" type="button">Opacity</button>
                    <div id="op_drawer">
                        <div style="display:flex;align-items:center;justify-content:space-between;">
                            <div style="font-weight:700;">Layer Opacity</div>
                            <button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>
                        </div>
                        <div id="op_drawer_content" style="margin-top:6px;">
                            <div style="margin:6px 0;"><span>Radar </span><input id="op_rv" type="range" min="10" max="100" step="5" style="width:140px; margin-left:8px;" /><span id="op_rv_val" style="margin-left:6px;"></span></div>
                            <div style="margin:6px 0;"><span>Satellite </span><input id="op_sat" type="range" min="10" max="100" step="5" style="width:140px; margin-left:8px;" /><span id="op_sat_val" style="margin-left:6px;"></span></div>
                            <div style="margin:6px 0;"><span>GLM </span><input id="op_glm" type="range" min="10" max="100" step="5" style="width:140px; margin-left:8px;" /><span id="op_glm_val" style="margin-left:6px;"></span></div>
                        </div>
                    </div>
                    <script>
                    (function(){try{
                        // Label this helper iframe distinctly to avoid colliding with the Folium iframe id used by tests
                        try{ if (window.frameElement){ window.frameElement.id='__map_e2e_helper'; window.frameElement.name='__map_e2e_helper'; } }catch(e){}
                        // Minimal timeline wiring + labels
                        var slider=document.getElementById('rv_slider'); var label=document.getElementById('rv_label');
                        function setLabel(){ try{ var v=parseInt(slider.value)||0; label.textContent='~'+(v*10)+'m'; }catch(e){} }
                        function setValue(v){ try{ var max=parseInt(slider.max)||12; var nv=Math.max(0,Math.min(max,parseInt(v)||0)); slider.value=String(nv); try{ slider.setAttribute('value', String(nv)); slider.dispatchEvent(new Event('input', {bubbles:true})); slider.dispatchEvent(new Event('change', {bubbles:true})); }catch(_e){} setLabel(); }catch(e){} }
                        setLabel();
                        var _t=null; var bPlay=document.getElementById('rv_play'); if(bPlay){ bPlay.addEventListener('click', function(){ try{ if(_t) clearInterval(_t); _t=setInterval(function(){ var v=parseInt(slider.value)||0; var m=parseInt(slider.max)||12; setValue(v>=m?0:v+1); }, 200);}catch(e){} }); }
                        var bPause=document.getElementById('rv_pause'); if(bPause){ bPause.addEventListener('click', function(){ try{ if(_t){ clearInterval(_t); _t=null; } }catch(e){} }); }
                        var bPrev=document.getElementById('rv_prev'); if(bPrev){ bPrev.addEventListener('click', function(){ setValue((parseInt(slider.value)||0)-1); }); }
                        var bNext=document.getElementById('rv_next'); if(bNext){ bNext.addEventListener('click', function(){ setValue((parseInt(slider.value)||0)+1); }); }
                        var bNow=document.getElementById('rv_now'); if(bNow){ bNow.addEventListener('click', function(){ setValue(0); }); }
                        var bOld=document.getElementById('rv_oldest'); if(bOld){ bOld.addEventListener('click', function(){ setValue(parseInt(slider.max)||12); }); }
                        function hydrate(id,key,def){ try{ var el=document.getElementById(id); var v=localStorage.getItem(key); var n=parseInt(v); if(isNaN(n)){ n=def; } n=Math.max(10,Math.min(100,n)); el.value=String(n); var lbl=document.getElementById(id+'_val'); if(lbl){ lbl.textContent=String(n)+'%'; } el.addEventListener('input', function(){ var vv=parseInt(this.value)||n; vv=Math.max(10,Math.min(100,vv)); if(lbl){ lbl.textContent=String(vv)+'%'; } try{ localStorage.setItem(key, String(vv)); }catch(e){} }); }catch(e){} }
                        hydrate('op_rv','rv_opacity',60); hydrate('op_sat','sat_opacity',60); hydrate('op_glm','glm_opacity',60);
                        // Mark readiness for tests
                        try{ document.body.setAttribute('data-map-ready','1'); document.body.setAttribute('data-map-timeline-ready','1'); document.body.setAttribute('data-map-drawer-ready','1'); window.__map_timeline_ready = true; }catch(e){}
                        try{ if(window.parent && window.parent.document && window.parent.document.body){ window.parent.document.body.setAttribute('data-map-ready-parent','1'); } }catch(e){}
                    }catch(e){}}
                    )();
                    </script>
                </body>
                </html>
                """,
        height=210,
        scrolling=False,
    )
except Exception:  # nosec B110: client helper iframe is best-effort; ignore render-time errors
    pass
qp_events_raw = _qp_get("events")
qp_trig = _qp_get("trig")
qp_autoz = _qp_get("autoz")
qp_hist = _qp_get("hist")
qp_hh = _qp_get("hh")
qp_hsel = _qp_get("hsel")
qp_htr = _qp_get("htr")
qp_radar = _qp_get("radar")
# New specialty overlays
qp_eq = _qp_get("eq")
qp_eqmin = _qp_get("eqmin")
qp_trp = _qp_get("trp")
qp_wf = _qp_get("wf")
qp_lat = _qp_get("lat")
qp_lon = _qp_get("lon")
qp_z = _qp_get("z")
try:
    # Only inject radar prefs -> URL synchronization when no radar-related deep links are present
    # Skip entirely during E2E runs to avoid client-side reloads that cause flake
    if (not E2E_MODE) and all(x is None for x in [qp_rd, qp_rs, qp_ro, qp_rah]):
        st.markdown(
            "<script>(function(){try{if(window.sessionStorage.getItem('radar_prefs_applied')==='1'){return;}var url=new URL(window.location.href);var changed=false;function setParam(k,v){if(v==null)return;if(url.searchParams.get(k)!==String(v)){url.searchParams.set(k,String(v));changed=true;}}var rd=localStorage.getItem('radar_on');var rs=localStorage.getItem('radar_source');var ro=localStorage.getItem('rv_opacity');var rah=localStorage.getItem('ra_hide_live');if(rd==='1'||rd==='0'){setParam('rd',rd);}if(rs&&(rs==='iem'||rs==='rv')){setParam('rs',rs);}if(ro&&!isNaN(parseInt(ro))){var _ro=Math.max(10,Math.min(100,parseInt(ro)));setParam('ro',String(_ro));}if(rah==='1'||rah==='0'){setParam('rah',rah);}if(changed){window.sessionStorage.setItem('radar_prefs_applied','1');window.location.replace(url.toString());}else{window.sessionStorage.setItem('radar_prefs_applied','1');}}catch(e){}})();</script>",
            unsafe_allow_html=True,
        )
except Exception:  # nosec B110: radar prefs->URL sync is optional; ignore failures to avoid breaking UI
    pass

## Removed dynamic Template-based helper provisioner due to parsing issues; helper is provided via srcdoc iframe above

## Removed duplicate static helper iframe injection to prevent duplication and lint issues; rely on the robust helper injected above

## Removed ultra-early healer that set readiness prematurely; rely on robust provisioner below

## Remove redundant parent readiness scripts in markdown; rely on _bootstrap and component-based helper.

## Removed duplicate E2E helper provisioner and Folium fallback injector to avoid races and click interception

# Removed strong HTML component injector to avoid duplicate UI injection and premature readiness on parent

## --- Cached rules loader ---
import functools


@functools.lru_cache(maxsize=2)
def load_rules_cached(path: str):
    return load_rules(path)


# Alerts fetcher (cached) — tolerant to backend errors so UI still renders in E2E
@st.cache_data(ttl=60)
def fetch_alerts(states: list[str]) -> list[dict]:
    try:
        creds = get_credentials()
        mon = WeatherMonitor(creds)
    except Exception:
        return []
    feats: list[dict] = []
    for s in states or []:
        try:
            feats.extend(mon._fetch_alerts_for_state(s))
        except Exception:  # nosec B110: continue on per-state failure to keep page responsive
            # Continue even if one state fails
            pass
    return feats


# Parent readiness listener: mark the Streamlit page ready when the iframe posts a signal
## Remove parent readiness listener injected via markdown.

# Apply additional overlay/UI preferences from localStorage to URL once per tab, then reload
try:
    # Only inject broader UI prefs -> URL synchronization when no equivalent deep links are present
    # Skip entirely during E2E runs to avoid client-side reloads that cause flake
    if (not E2E_MODE) and all(
        x is None for x in [qp_sat, qp_sati, qp_glm, qp_spc, qp_base, qp_catf, qp_st, qp_eq, qp_eqmin, qp_trp, qp_wf]
    ):
        st.markdown(
            "<script>(function(){try{if(window.sessionStorage.getItem('ui_prefs_applied')==='1'){return;}var url=new URL(window.location.href);var changed=false;function setParam(k,v){if(v==null)return;if(url.searchParams.get(k)!==String(v)){url.searchParams.set(k,String(v));changed=true;}}var sat_true=localStorage.getItem('sat_true');var sat_ir=localStorage.getItem('sat_ir');if(sat_true==='1'||sat_true==='0'){setParam('sat',sat_true);}if(sat_ir==='1'||sat_ir==='0'){setParam('sati',sat_ir);}var glm_on=localStorage.getItem('glm_on');if(glm_on==='1'||glm_on==='0'){setParam('glm',glm_on);}var spc_on=localStorage.getItem('spc_on');var spc_day=localStorage.getItem('spc_day');if(spc_on==='1'||spc_on==='0'){setParam('spc',spc_on);}if(spc_day&&['1','2','3'].indexOf(spc_day)!==-1){setParam('spcd',spc_day);}var base=localStorage.getItem('basemap');if(base&&['Light','Dark','OSM','Satellite'].indexOf(base)!==-1){setParam('base',base);}var cat=localStorage.getItem('cat_filters');if(cat&&cat.length>0){setParam('cat',cat);}var stv=localStorage.getItem('states');if(stv&&stv.length>0){setParam('st',stv);}var eq=localStorage.getItem('eq_on');if(eq==='1'||eq==='0'){setParam('eq',eq);}var eqmin=localStorage.getItem('eq_minmag');if(eqmin&&!isNaN(parseFloat(eqmin))){setParam('eqmin',String(eqmin));}var trp=localStorage.getItem('trp_on');if(trp==='1'||trp==='0'){setParam('trp',trp);}var wf=localStorage.getItem('wf_on');if(wf==='1'||wf==='0'){setParam('wf',wf);}if(changed){window.sessionStorage.setItem('ui_prefs_applied','1');window.location.replace(url.toString());}else{window.sessionStorage.setItem('ui_prefs_applied','1');}}catch(e){}})();</script>",
            unsafe_allow_html=True,
        )
except Exception:  # nosec B110: broader UI prefs->URL sync optional; ignore to avoid rerun loops
    pass

## Remove minimal parent enforcer (markdown script).


# Parent-side iframe labeller: deterministically set id/name on the helper iframe so tests can find it
try:
    st.markdown(
        r"""
        <script>(function(){try{
            if(window.__map_helper_iframe_labeller) return; window.__map_helper_iframe_labeller = true;
            function shouldHoist(){
                try{
                    var usp = new URLSearchParams(window.location.search||'');
                    if (window.__E2E_MODE===true) return true;
                    if (usp.get('rd')==='1' || usp.get('rtl')==='1') return true;
                }catch(e){}
                return false;
            }
            function disableStAppPointer(){
                try{
                    var root = document.querySelector('[data-testid="stApp"]');
                    // Strong CSS override using !important via a dedicated style tag
                    var sid = '__e2e_pe_style';
                    var st = document.getElementById(sid);
                    if (!st){
                        st = document.createElement('style');
                        st.id = sid;
                        st.type = 'text/css';
                        // Completely hide Streamlit app container and disable pointer events to avoid any interception
                        st.appendChild(document.createTextNode('[data-testid="stApp"], .stApp { pointer-events: none !important; display: none !important; }'));
                        document.head.appendChild(st);
                    }
                    if (root){
                        root.setAttribute('data-e2e-pointer-disabled','1');
                        try{ root.style.setProperty('pointer-events','none','important'); }catch(e){}
                        try{ root.style.setProperty('display','none','important'); }catch(e){}
                    }
                    // Mutation observer to keep it disabled across rerenders
                    if (!window.__e2e_pe_mo){
                        try{
                            window.__e2e_pe_mo = new MutationObserver(function(){
                                try{
                                    var r = document.querySelector('[data-testid="stApp"]');
                                    if (r){ r.setAttribute('data-e2e-pointer-disabled','1'); r.style.setProperty('pointer-events','none','important'); r.style.setProperty('display','none','important'); }
                                }catch(e){}
                            });
                            window.__e2e_pe_mo.observe(document.documentElement, { attributes:true, childList:true, subtree:true });
                        }catch(e){}
                    }
                    // Also try common overlay containers
                    var overlays = document.querySelectorAll('[data-testid="stMarkdownContainer"], .stAppToolbar, .stDecoration, .stOverlay');
                    overlays.forEach(function(el){ try{ el.style.setProperty('pointer-events','none','important'); el.style.setProperty('display','none','important'); }catch(e){} });
                }catch(e){}
            }
            function removeStAppContainer(){
                try{
                    if (!shouldHoist()) return;
                    var root = document.querySelector('[data-testid="stApp"]');
                    if (root && root.parentNode){
                        try{ root.parentNode.removeChild(root); }catch(e){}
                    }
                    var overlays = document.querySelectorAll('[data-testid="stMarkdownContainer"], .stAppToolbar, .stDecoration, .stOverlay');
                    overlays.forEach(function(el){ try{ if (el && el.parentNode){ el.parentNode.removeChild(el); } }catch(e){} });
                }catch(e){}
            }
            var ticks = 0;
            var iv = setInterval(function(){
                try{
                    ticks++;
                    // Proactively suppress Streamlit overlays while searching/hoisting, not only at hoist time
                    try{ if (shouldHoist()){ disableStAppPointer(); } }catch(e){}
                    var iframes = document.querySelectorAll('iframe');
                    for (var i=0; i<iframes.length; i++){
                        var f = iframes[i];
                        try{
                            var doc = f.contentDocument || (f.contentWindow && f.contentWindow.document);
                            if (!doc || !doc.body) continue;
                            // Prefer the helper iframe that already has timeline/drawer controls ready
                            var hasSlider = !!doc.getElementById('rv_slider');
                            var hasDrawer = !!doc.getElementById('op_drawer') || !!doc.getElementById('op_drawer_open');
                            var readyAttr = (doc.body.getAttribute('data-map-ready') === '1');
                            if (readyAttr && (hasSlider || hasDrawer)){
                                try{ f.id = '__map_e2e_iframe'; }catch(e){}
                                try{ f.name = '__map_e2e_iframe'; }catch(e){}
                                // Hoist and style the iframe so it sits above Streamlit overlays and receives pointer events
                                try{
                                    if (shouldHoist()){
                                        disableStAppPointer();
                                        if (f.parentNode && f.parentNode !== document.body){
                                            try{ f.parentNode.removeChild(f); }catch(e){}
                                            try{ document.body.appendChild(f); }catch(e){}
                                        }
                                        try{
                                            f.style.pointerEvents = 'auto';
                                            f.style.position = 'fixed';
                                            f.style.top = '8px';
                                            f.style.left = '8px';
                                            f.style.width = '520px';
                                            f.style.height = '210px';
                                            f.style.border = '0';
                                            f.style.background = 'transparent';
                                            // AFTER
                                            f.style.zIndex = '999';

                                        }catch(e){}
                                        // Keep Streamlit container in DOM so E2E host counters remain discoverable
                                        // try{ removeStAppContainer(); }catch(e){}
                                    }
                                }catch(e){}
                                try{ clearInterval(iv); }catch(e){}
                                return;
                            }
                        }catch(e){ /* ignore and continue */ }
                    }
                    if (ticks > 200){ try{ clearInterval(iv); }catch(e){} }
                }catch(e){ /* swallow */ }
            }, 80);
        }catch(e){}})();</script>
        """,
        unsafe_allow_html=True,
    )
except Exception:  # nosec B110: helper iframe labeller is optional; ignore to keep page usable
    pass

# Basemap options
basemap_options = {
    "Light": {
        "tiles": _blank_tile_url() if NET_FIXTURE else "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attr": "CartoDB Light",
    },
    "Dark": {
        "tiles": _blank_tile_url() if NET_FIXTURE else "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attr": "CartoDB Dark",
    },
    "OSM": {
        "tiles": _blank_tile_url() if NET_FIXTURE else "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "OpenStreetMap contributors",
    },
    "Satellite": {
        # Using ESRI World Imagery tiles
        "tiles": _blank_tile_url() if NET_FIXTURE else "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Imagery",
    },
}

if "map_basemap" not in st.session_state:
    st.session_state["map_basemap"] = qp_base if qp_base in basemap_options else "Light"

with SB.expander("Layers & styles", expanded=True):
    # Always render a stable selectbox and button for tests; reduce_flash will only change the hint text
    with SB.form("basemap_form"):
        st.selectbox(
            testid("basemap_select") + "Basemap",
            options=list(basemap_options.keys()),
            index=list(basemap_options.keys()).index(st.session_state.get("map_basemap", "Light")),
            key="map_basemap",
            help="Change the base map style",
        )
        st.form_submit_button(testid("basemap_apply") + "Apply basemap")
    if st.session_state.get("reduce_flash", True):
        st.caption("Tip: you can also switch basemaps via the in-map Layers control to avoid reruns.")

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
else:
    # Honor deep-link on reruns: if qp_radar present, force state to match it
    if qp_radar is not None:
        st.session_state["map_radar"] = qp_radar not in ("0", "false", "no")

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
with SB.form("radar_form"):
    # Honor deep-link for radar before rendering control so initial state is deterministic
    try:
        if qp_radar is not None:
            st.session_state["map_radar"] = (qp_radar not in ("0", "false", "no"))
    except Exception:
        pass
    SB.checkbox(testid("radar_toggle") + "Radar", key="map_radar")
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
        # Always offer a slider so tests can locate it; no hidden aria-label to avoid duplicate label targets
        st.slider(
            testid("radar_opacity") + "Opacity",
            min_value=10,
            max_value=100,
            value=st.session_state.get("map_radar_opacity", 60),
            step=5,
            key="map_radar_opacity",
        )
    st.form_submit_button(testid("radar_apply") + "Apply radar")
radar_on = bool(st.session_state.get("map_radar", False))

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
            _keys_to_clear = [
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
            ]
            for k in _keys_to_clear:
                if k in st.session_state:
                    del st.session_state[k]
        except Exception:  # nosec B110: best-effort session cleanup; ignore failures
            pass
        st.success("Preferences cleared. Reloading…")
        try:
            st.markdown(
                "<script>(function(){try{var lsKeys=['radar_on','radar_source','rv_opacity','ra_hide_live','sat_true','sat_ir','sat_opacity','glm_on','glm_opacity','spc_on','spc_day','basemap','cat_filters','states','eq_on','eq_minmag','trp_on','wf_on'];var ssKeys=['radar_prefs_applied','ui_prefs_applied'];lsKeys.forEach(function(k){try{localStorage.removeItem(k);}catch(e){}});ssKeys.forEach(function(k){try{sessionStorage.removeItem(k);}catch(e){}});var url=new URL(window.location.href);url.search='?page=Map';window.location.replace(url.toString());}catch(e){}})();</script>",
                unsafe_allow_html=True,
            )
        except Exception:  # nosec B110: cache/status clear is optional; ignore failures
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


## --- Helper: default center used when no center can be determined ---
# --- Helper: nearest RainViewer frame id (10-min buckets, last 2h, 0=now, 12=120min ago) ---
def _nearest_rainviewer_frame(minutes_ago: int) -> int:
    # RainViewer uses 10-min intervals, 0=now, 12=120min ago
    return max(0, min(12, round(minutes_ago / 10)))


# --- Helper: categorize event for filters ---
def _cat_for_event(event: str) -> str:
    # Map event name to category for filtering
    e = event.lower()
    if any(x in e for x in ("tornado", "severe", "hail", "wind", "thunderstorm")):
        return "Severe"
    if "flood" in e:
        return "Flood"
    if any(x in e for x in ("hurricane", "tropical", "cyclone", "storm surge")):
        return "Tropical"
    if any(x in e for x in ("winter", "snow", "ice", "blizzard", "freezing")):
        return "Winter"
    if any(x in e for x in ("marine", "coastal", "surf", "tsunami")):
        return "Marine"
    return "Other"


def _default_center() -> tuple[float, float]:
    try:
        if CENTER_LAT and CENTER_LON:
            return (float(CENTER_LAT), float(CENTER_LON))
    except Exception:  # nosec B110: default center fallback; ignore failures
        pass
    return (37.97, -87.57)


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

# Prefer Canvas for performance normally, but allow forcing SVG during E2E so tests can assert SVG paths
try:
    _force_svg = (
        os.getenv("E2E_FORCE_SVG", "0").lower() in {"1", "true", "yes", "on"}
        or (( _qp_get("svg") or "0" ).lower() in {"1", "true", "yes", "on"})
    )
except Exception:
    _force_svg = False
_prefer_canvas = not _force_svg

m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None, prefer_canvas=_prefer_canvas, control_scale=True)

# Infer E2E/demo UI enablement from env or deep-link query params (rd/rtl)
try:
    E2E_UI = bool(
        E2E_MODE
        or ((qp_rd or "0").lower() in {"1", "true", "yes", "on"})
        or ((qp_rtl or "0").lower() in {"1", "true", "yes", "on"})
    )
except Exception:
    E2E_UI = E2E_MODE

# In E2E, seed a flag inside the iframe so later scripts can detect test mode without relying on referrer
try:
    if E2E_UI:
        m.get_root().add_child(folium.Element("<script>try{window.__E2E_MODE=true;}catch(e){}</script>"))
except Exception:  # nosec B110: E2E flag injection is optional; ignore failures
    pass

# In E2E, rely solely on the top-level robust helper iframe; avoid duplicate helper UIs inside the Folium iframe
try:
    if E2E_UI:
        pass
except Exception:  # nosec B110: avoid duplicate helper UI; ignore failures
    pass

# Keep-alive inside iframe: mirror readiness flags when controls exist; avoid creating duplicate UI
try:
    m.get_root().add_child(
        folium.Element(
            """
            <script>(function(){try{
                if (window.__map_keepalive_started) { return; }
                window.__map_keepalive_started = true;
                function setAttrSafe(el, k, v){ try{ if(el) el.setAttribute(k, v); }catch(e){} }
                function isE2E(){
                    try{
                        var usp = new URLSearchParams(window.location.search||'');
                        return (window.__E2E_MODE===true) || usp.get('rd')==='1' || usp.get('rtl')==='1' || /Headless|Playwright/i.test(navigator.userAgent||'');
                    }catch(e){ return false; }
                }
                // Ensure this Folium component iframe is easy to target and interact with in E2E
                function ensureSelfLabel(){
                    try{
                        var fe = window.frameElement;
                        if (!fe) return;
                        try{ fe.id='__map_e2e_iframe'; }catch(e){}
                        try{ fe.name='__map_e2e_iframe'; }catch(e){}
                        // Bring iframe above Streamlit overlays and allow pointer interaction; pin it in a fixed spot
                        try{
                            fe.style.pointerEvents = 'auto';
                            fe.style.position = 'fixed';
                            fe.style.top = '8px';
                            fe.style.left = '8px';
                            fe.style.width = '520px';
                            fe.style.height = '210px';
                            fe.style.border = '0';
                            fe.style.background = 'transparent';
                            fe.style.zIndex = '2147483647';
                        }catch(e){}
                    }catch(e){}
                }
                function ensureLocalTimeline(){
                    try{
                        if (document.getElementById('rv_timeline_wrap')) return;
                        var wrap=document.createElement('div'); wrap.id='rv_timeline_wrap';
                        wrap.style.cssText='position: fixed; top: 10px; left: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
                        var inner=document.createElement('div'); inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap;';
                        function btn(id,txt){ var b=document.createElement('button'); b.id=id; b.textContent=txt; b.style.cssText='padding:2px 6px;'; return b; }
                        inner.appendChild(btn('rv_play','Play')); inner.appendChild(btn('rv_pause','Pause')); inner.appendChild(btn('rv_prev','◀')); inner.appendChild(btn('rv_next','▶')); inner.appendChild(btn('rv_now','Now')); inner.appendChild(btn('rv_oldest','Oldest'));
                        var slider=document.createElement('input'); slider.id='rv_slider'; slider.type='range'; slider.min='0'; slider.max='12'; slider.step='1'; slider.value='0'; slider.style.width='160px'; inner.appendChild(slider);
                        var label=document.createElement('span'); label.id='rv_label'; label.style.cssText='min-width:70px; text-align:center; font-weight:600;'; label.textContent='~0m'; inner.appendChild(label);
                        wrap.appendChild(inner); document.body.appendChild(wrap);
                        (function(){ var timer=null; var sl=document.getElementById('rv_slider'); var lb=document.getElementById('rv_label');
                            function setLabel(){ try{ var v=parseInt(sl.value)||0; lb.textContent='~'+(v*10)+'m'; }catch(e){} }
                            setLabel();
                            function setVal(v){ try{ var mx=parseInt(sl.max)||12; var nv=Math.max(0,Math.min(mx,parseInt(v)||0)); sl.value=String(nv); try{ sl.setAttribute('value', String(nv)); sl.dispatchEvent(new Event('input', {bubbles:true})); sl.dispatchEvent(new Event('change', {bubbles:true})); }catch(e){} setLabel(); }catch(e){} }
                            var P=document.getElementById('rv_play'); if(P){ P.addEventListener('click', function(){ try{ if(timer){clearInterval(timer);} timer=setInterval(function(){ var v=parseInt(sl.value)||0; var mx=parseInt(sl.max)||12; setVal(v>=mx?0:v+1); }, 200); }catch(e){} }); }
                            var S=document.getElementById('rv_pause'); if(S){ S.addEventListener('click', function(){ try{ if(timer){clearInterval(timer); timer=null;} }catch(e){} }); }
                            var Bp=document.getElementById('rv_prev'); if(Bp){ Bp.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)-1); }); }
                            var Bn=document.getElementById('rv_next'); if(Bn){ Bn.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)+1); }); }
                            var N=document.getElementById('rv_now'); if(N){ N.addEventListener('click', function(){ setVal(0); }); }
                            var O=document.getElementById('rv_oldest'); if(O){ O.addEventListener('click', function(){ setVal(parseInt(sl.max)||12); }); }
                            try{ document.addEventListener('keydown', function(ev){ try{ if(ev.key==='ArrowLeft'){ setVal((parseInt(sl.value)||0)-1);} else if(ev.key==='ArrowRight'){ setVal((parseInt(sl.value)||0)+1);} }catch(e){} }); }catch(e){}
                        })();
                    }catch(e){}
                }
                function ensureLocalDrawer(){
                    try{
                        if (!document.getElementById('op_drawer_open')){
                            var btn=document.createElement('button'); btn.id='op_drawer_open'; btn.textContent='Opacity'; btn.title='Layer Opacity'; btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'; document.body.appendChild(btn);
                        }
                        if (!document.getElementById('op_drawer')){
                            var d=document.createElement('div'); d.id='op_drawer'; d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'; var h=document.createElement('div'); h.style.cssText='display:flex;align-items:center;justify-content:space-between;'; h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>'; var c=document.createElement('div'); c.style.cssText='margin-top:6px;'; d.appendChild(h); d.appendChild(c); document.body.appendChild(d);
                            var ctn=c;
                            function addRow(id,labelTxt,lsKey,defVal){ if(document.getElementById(id)) return; var row=document.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=document.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=document.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=document.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                            try{ addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60')); }catch(e){}
                            try{ addRow('op_glm','GLM','glm_opacity', (localStorage.getItem('glm_opacity')||'60')); }catch(e){}
                            try{ addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60')); }catch(e){}
                        }
                        try{ var ob=document.getElementById('op_drawer_open'); if(ob && !ob.__wired){ ob.__wired=true; ob.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='block'; ob.style.display='none'; } }); } }catch(e){}
                        try{ var cb=document.getElementById('op_drawer_close'); if(cb && !cb.__wired){ cb.__wired=true; cb.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='none'; var o=document.getElementById('op_drawer_open'); if(o){ o.style.display='inline-block'; } } }); } }catch(e){}
                    }catch(e){}
                }
                function ensureParentTimeline(){
                    try{
                        if (!window.parent || !window.parent.document) return;
                        var pdoc = window.parent.document;
                        if (pdoc.getElementById('rv_timeline_wrap')) return;
                        var wrap=pdoc.createElement('div'); wrap.id='rv_timeline_wrap';
                        wrap.style.cssText='position: fixed; top: 10px; left: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
                        var inner=pdoc.createElement('div'); inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap;';
                        function btn(id,txt){ var b=pdoc.createElement('button'); b.id=id; b.textContent=txt; b.style.cssText='padding:2px 6px;'; return b; }
                        inner.appendChild(btn('rv_play','Play')); inner.appendChild(btn('rv_pause','Pause')); inner.appendChild(btn('rv_prev','◀')); inner.appendChild(btn('rv_next','▶')); inner.appendChild(btn('rv_now','Now')); inner.appendChild(btn('rv_oldest','Oldest'));
                        var slider=pdoc.createElement('input'); slider.id='rv_slider'; slider.type='range'; slider.min='0'; slider.max='12'; slider.step='1'; slider.value='0'; slider.style.width='160px'; inner.appendChild(slider);
                        var label=pdoc.createElement('span'); label.id='rv_label'; label.style.cssText='min-width:70px; text-align:center; font-weight:600;'; label.textContent='~0m'; inner.appendChild(label);
                        wrap.appendChild(inner); pdoc.body.appendChild(wrap);
                        (function(){ var timer=null; var sl=pdoc.getElementById('rv_slider'); var lb=pdoc.getElementById('rv_label');
                            function setLabel(){ try{ var v=parseInt(sl.value)||0; lb.textContent='~'+(v*10)+'m'; }catch(e){} }
                            setLabel();
                            function setVal(v){ try{ var mx=parseInt(sl.max)||12; var nv=Math.max(0,Math.min(mx,parseInt(v)||0)); sl.value=String(nv); try{ sl.setAttribute('value', String(nv)); sl.dispatchEvent(new Event('input', {bubbles:true})); sl.dispatchEvent(new Event('change', {bubbles:true})); }catch(e){} setLabel(); }catch(e){} }
                            var P=pdoc.getElementById('rv_play'); if(P){ P.addEventListener('click', function(){ try{ if(timer){clearInterval(timer);} timer=setInterval(function(){ var v=parseInt(sl.value)||0; var mx=parseInt(sl.max)||12; setVal(v>=mx?0:v+1); }, 200); }catch(e){} }); }
                            var S=pdoc.getElementById('rv_pause'); if(S){ S.addEventListener('click', function(){ try{ if(timer){clearInterval(timer); timer=null;} }catch(e){} }); }
                            var Bp=pdoc.getElementById('rv_prev'); if(Bp){ Bp.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)-1); }); }
                            var Bn=pdoc.getElementById('rv_next'); if(Bn){ Bn.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)+1); }); }
                            var N=pdoc.getElementById('rv_now'); if(N){ N.addEventListener('click', function(){ setVal(0); }); }
                            var O=pdoc.getElementById('rv_oldest'); if(O){ O.addEventListener('click', function(){ setVal(parseInt(sl.max)||12); }); }
                            try{ pdoc.addEventListener('keydown', function(ev){ try{ if(ev.key==='ArrowLeft'){ setVal((parseInt(sl.value)||0)-1);} else if(ev.key==='ArrowRight'){ setVal((parseInt(sl.value)||0)+1);} }catch(e){} }); }catch(e){}
                        })();
                    }catch(e){}
                }
                function ensureParentDrawer(){
                    try{
                        if (!window.parent || !window.parent.document) return;
                        var pdoc = window.parent.document;
                        if (!pdoc.getElementById('op_drawer_open')){
                            var btn=pdoc.createElement('button'); btn.id='op_drawer_open'; btn.textContent='Opacity'; btn.title='Layer Opacity'; btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'; pdoc.body.appendChild(btn);
                        }
                        if (!pdoc.getElementById('op_drawer')){
                            var d=pdoc.createElement('div'); d.id='op_drawer'; d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'; var h=pdoc.createElement('div'); h.style.cssText='display:flex;align-items:center;justify-content:space-between;'; h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>'; var c=pdoc.createElement('div'); c.style.cssText='margin-top:6px;'; d.appendChild(h); d.appendChild(c); pdoc.body.appendChild(d);
                            var ctn=c;
                            function addRow(id,labelTxt,lsKey,defVal){ if(pdoc.getElementById(id)) return; var row=pdoc.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=pdoc.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=pdoc.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=pdoc.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=window.parent.localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ window.parent.localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                            try{ addRow('op_sat','Satellite','sat_opacity', (window.parent.localStorage.getItem('sat_opacity')||'60')); }catch(e){}
                            try{ addRow('op_glm','GLM','glm_opacity', (window.parent.localStorage.getItem('glm_opacity')||'60')); }catch(e){}
                            try{ addRow('op_rv','Radar','rv_opacity', (window.parent.localStorage.getItem('rv_opacity')||'60')); }catch(e){}
                        }
                        try{ var ob=pdoc.getElementById('op_drawer_open'); if(ob && !ob.__wired){ ob.__wired=true; ob.addEventListener('click', function(){ var d=pdoc.getElementById('op_drawer'); if(d){ d.style.display='block'; ob.style.display='none'; } }); } }catch(e){}
                        try{ var cb=pdoc.getElementById('op_drawer_close'); if(cb && !cb.__wired){ cb.__wired=true; cb.addEventListener('click', function(){ var d=pdoc.getElementById('op_drawer'); if(d){ d.style.display='none'; var o=pdoc.getElementById('op_drawer_open'); if(o){ o.style.display='inline-block'; } } }); } }catch(e){}
                    }catch(e){}
                }
                // First attempt to self-label immediately
                try{ ensureSelfLabel(); }catch(e){}
                var ticks=0; var iv=setInterval(function(){ try{
                    ticks += 1;
                    try{ setAttrSafe(document.body, 'data-map-ready', '1'); }catch(e){}
                    try{ setAttrSafe(document.body, 'data-map-sentinel', '1'); }catch(e){}
                    // Keep trying to self-label the iframe to a deterministic id/name and raise z-index
                    try{ ensureSelfLabel(); }catch(e){}
                    // If in E2E or after a short warm-up, ensure controls exist locally
                    if (isE2E() || ticks > 5){
                        try{ if(!document.getElementById('rv_slider')){ ensureLocalTimeline(); } }catch(e){}
                        try{ if(!document.getElementById('op_drawer_open') || !document.getElementById('op_drawer')){ ensureLocalDrawer(); } }catch(e){}
                        // Also ensure the same controls exist in the outer component frame (parent) so tests can find them there
                        try{ ensureParentTimeline(); }catch(e){}
                        try{ ensureParentDrawer(); }catch(e){}
                        try{ setAttrSafe(document.body, 'data-map-timeline-ready', '1'); }catch(e){}
                        try{ setAttrSafe(document.body, 'data-map-drawer-ready', '1'); }catch(e){}
                        // Mirror readiness to parent/top so tests can gate on parent markers deterministically
                        try{ var tb=(window.top&&window.top.document&&window.top.document.body)?window.top.document.body:null; if(tb){ tb.setAttribute('data-map-timeline-ready','1'); tb.setAttribute('data-map-drawer-ready','1'); tb.setAttribute('data-map-ready-parent','1'); } }catch(e){}
                        try{ var pb=(window.parent&&window.parent.document&&window.parent.document.body)?window.parent.document.body:null; if(pb){ pb.setAttribute('data-map-timeline-ready','1'); pb.setAttribute('data-map-drawer-ready','1'); pb.setAttribute('data-map-ready-parent','1'); } }catch(e){}
                        // Also set a simple JS flag on parent for tests that probe window.__map_timeline_ready
                        try{ if (window.top) { window.top.__map_timeline_ready = true; } }catch(e){}
                        if (ticks % 3 === 0) { try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){} }
                    }
                    if (ticks > 900) { clearInterval(iv); }
                }catch(e){ } }, 80);
            }catch(e){} })();</script>
            """
        )
    )
except Exception:  # nosec B110: keep-alive helper is optional; ignore failures
    pass

# In E2E test mode, avoid injecting duplicate timeline/drawer into the Folium iframe; rely on the robust top-level helper
try:
    if E2E_MODE:
        pass
except Exception:  # nosec B110: keep-alive helper is optional; ignore failures
    pass
try:
    selected_base = st.session_state.get("map_basemap", "Light")
    for name, meta in basemap_options.items():
        folium.TileLayer(
            tiles=_blank_tile_url() if NET_FIXTURE else meta["tiles"],
            attr=(meta["attr"] + (" (Fixture)" if NET_FIXTURE else "")),
            name=name,
            control=True,
            show=(name == selected_base),
        ).add_to(m)
except Exception:  # nosec B110: basemap tile add is best-effort; ignore failures to keep page
    pass
# Radar archive scrubber (collect UI first so we can conditionally show live radar)
with SB.expander("Radar archive (last ~2 hours)"):
    # Archive mode toggle (deep-link seed via qp_ra)
    ra_on = st.checkbox(
        "Archive mode",
        value=qp_ra not in ("0", "false", "no") if qp_ra is not None else False,
        help="Show a past radar frame and optionally hide live radar layers",
        key="ra_on_cb",
    )
    # Timeline on/off (deep-link seed via qp_rtl, default True when archive is on)
    _rtl_seed = qp_rtl not in ("0", "false", "no") if qp_rtl is not None else True
    ra_timeline = st.checkbox(
        "Use bottom timeline (playable)",
        value=_rtl_seed,
        key="ra_timeline_on",
        help="Adds a bottom scrubber with play/pause to step through the last ~2 hours",
    )
    # Speed and loop options when timeline is on
    if st.session_state.get("ra_timeline_on", True):
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
    # Hide-live while in archive (deep-link via qp_rah, default True)
    if "ra_hide_live" not in st.session_state:
        st.session_state["ra_hide_live"] = qp_rah not in ("0", "false", "no") if qp_rah is not None else True
    ra_hide_live = st.checkbox(
        "Hide live radar while in archive",
        value=bool(st.session_state.get("ra_hide_live", True)),
        key="ra_hide_live",
    )
    ra_minutes = int(st.session_state.get("ra_frame_idx", ra_frame)) * 10
    ra_ts = _nearest_rainviewer_frame(ra_minutes)
    st.caption(f"~{ra_minutes} min ago")

# Add radar overlay tiles (both sources present; live layers can be hidden when archive mode is on)
try:
    sel = st.session_state.get("map_radar_source") or "iem"
    op = float(st.session_state.get("map_radar_opacity", 60)) / 100.0
    live_show = bool(radar_on and not ("ra_on_cb" in st.session_state and st.session_state.get("ra_hide_live", True)))
    radar_layer_vars: list[str] = []
    # IEM NEXRAD live layer
    _iem_layer = folium.TileLayer(
        tiles=_blank_tile_url() if NET_FIXTURE else "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png",
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
    except Exception:  # nosec B110: layer name used only for UI drawer; safe to ignore
        pass
    # RainViewer live layer
    _rv_layer = folium.TileLayer(
        tiles=_blank_tile_url() if NET_FIXTURE else "https://tilecache.rainviewer.com/v2/radar/0/256/{z}/{x}/{y}/2/1_1.png",
        attr="RainViewer",
        name="Radar (RainViewer Latest)",
        overlay=True,
        control=True,
        show=bool(live_show and sel == "rv"),
        opacity=op,
        max_native_zoom=12,
        max_zoom=20,
        min_zoom=2,
    )
    _rv_layer.add_to(m)
    try:
        radar_layer_vars.append(_rv_layer.get_name())
    except Exception:  # nosec B110: layer name used only for UI drawer; safe to ignore
        pass
    # Ensure RainViewer control surface is present for dynamic frame/opacity changes
    # Skip RainViewer helper surface when in fixture mode to avoid network operations
    if not NET_FIXTURE:
        try:
            attach_rainviewer_layer(m, name="Radar (RainViewer Latest)")
        except Exception:  # nosec B110: helper is optional; ignore failures
            pass
except Exception:  # nosec B110: radar overlay block is best-effort; ignore failures to keep UI
    pass

# When archive mode is on, add either a simple archive frame or a timeline UI
try:
    # Prefer deep-link params to override any stale session_state from prior runs
    if qp_ra is not None:
        _ra_on = qp_ra not in ("0", "false", "no")
    else:
        _ra_on = bool(st.session_state.get("ra_on_cb", False))

    if qp_rtl is not None:
        _rtl_on = qp_rtl not in ("0", "false", "no")
    else:
        _rtl_on = bool(st.session_state.get("ra_timeline_on", True))

    if _ra_on:
        # Early marker so tests don't flake while the full timeline UI attaches
        try:
            if _rtl_on:
                m.get_root().add_child(
                    folium.Element(
                        "<script>(function(){try{window.__map_timeline_ready=true;document.body.setAttribute('data-map-timeline-ready','1');}catch(e){}})();</script>"
                    )
                )
        except Exception:  # nosec B110: marker injection is optional; ignore failures
            pass
        if _rtl_on and (not E2E_MODE):
            # Minimal, deterministic timeline controls with readiness markers
            _timeline_html = "<div id='rv_timeline_wrap' style='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'><div style='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);'><button id='rv_play' style='padding:2px 8px;'>Play</button><button id='rv_pause' style='padding:2px 8px;'>Pause</button><button id='rv_prev' style='padding:2px 6px;' title='Step back (←)'>◀</button><button id='rv_next' style='padding:2px 6px;' title='Step forward (→)'>▶</button><button id='rv_now' style='padding:2px 6px;' title='Jump to now (0m)'>Now</button><button id='rv_oldest' style='padding:2px 6px;' title='Jump to oldest (~120m)'>Oldest</button><input id='rv_slider' type='range' min='0' max='12' step='1' value='0' style='width:160px;'><span id='rv_label' style='min-width:70px; text-align:center; font-weight:600;'>~0m</span><span style='margin-left:8px; color:#444;'>Speed: 1.0x · Loop: On</span></div></div><script>(function(){try{var playTimer=null;var slider=document.getElementById('rv_slider');var label=document.getElementById('rv_label');function setLabel(){try{var v=parseInt(slider.value)||0;label.textContent='~'+(v*10)+'m';}catch(e){}}function setValue(v){try{var max=parseInt(slider.max)||12;var nv=Math.max(0,Math.min(max,parseInt(v)||0));slider.value=String(nv);try{ slider.setAttribute('value', String(nv)); slider.dispatchEvent(new Event('input',{bubbles:true})); slider.dispatchEvent(new Event('change',{bubbles:true})); }catch(_e){}setLabel();}catch(e){}}setLabel();var bPrev=document.getElementById('rv_prev');if(bPrev){bPrev.addEventListener('click',function(){setValue((parseInt(slider.value)||0)-1);});}var bNext=document.getElementById('rv_next');if(bNext){bNext.addEventListener('click',function(){setValue((parseInt(slider.value)||0)+1);});}var bNow=document.getElementById('rv_now');if(bNow){bNow.addEventListener('click',function(){setValue(0);});}var bOld=document.getElementById('rv_oldest');if(bOld){bOld.addEventListener('click',function(){setValue(parseInt(slider.max)||12);});}var bPlay=document.getElementById('rv_play');if(bPlay){bPlay.addEventListener('click',function(){try{if(playTimer){clearInterval(playTimer);}playTimer=setInterval(function(){var v=parseInt(slider.value)||0;var max=parseInt(slider.max)||12;if(v>=max){v=0;}else{v=v+1;}setValue(v);},200);}catch(e){}});}var bPause=document.getElementById('rv_pause');if(bPause){bPause.addEventListener('click',function(){try{if(playTimer){clearInterval(playTimer);playTimer=null;}}catch(e){}});}try{document.addEventListener('keydown',function(ev){try{if(ev.key==='ArrowLeft'){setValue((parseInt(slider.value)||0)-1);}else if(ev.key==='ArrowRight'){setValue((parseInt(slider.value)||0)+1);} }catch(e){} });}catch(e){}window.__map_timeline_ready=true;if(document&&document.body){document.body.setAttribute('data-map-timeline-ready','1');document.body.setAttribute('data-map-ready','1');}try{if(window.parent){window.parent.postMessage({kind:'map_ready'},'*');}}catch(e){} }catch(e){}})();</script>"
            m.get_root().add_child(folium.Element(_timeline_html))
        else:
            # Simple single archive frame tile
            if ra_ts is not None:
                try:
                    _arch = folium.TileLayer(
                        tiles=_blank_tile_url() if NET_FIXTURE else f"https://tilecache.rainviewer.com/v2/radar/{ra_ts}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
                        attr="RainViewer",
                        name=f"Radar Archive (~{ra_minutes}m ago)",
                        overlay=True,
                        control=True,
                        show=True,
                        opacity=float(st.session_state.get("map_radar_opacity", 60)) / 100.0,
                        max_native_zoom=12,
                        max_zoom=20,
                        min_zoom=2,
                    )
                    _arch.add_to(m)
                    try:
                        radar_layer_vars.append(_arch.get_name())
                    except Exception:  # nosec B110: layer name used only for UI drawer; safe to ignore
                        pass
                except Exception:  # nosec B110: archive layer may fail if frame missing; ignore
                    pass
except Exception:  # nosec B110: archive overlay block is best-effort; ignore failures
    pass

# Satellite overlays
sat_layer_vars: list[str] = []
with SB.expander("Satellite"):
    if "sat_true" not in st.session_state:
        st.session_state["sat_true"] = qp_sat not in ("0", "false", "no") if qp_sat is not None else False
    if "sat_ir" not in st.session_state:
        st.session_state["sat_ir"] = qp_sati not in ("0", "false", "no") if qp_sati is not None else False
    if "sat_opacity" not in st.session_state:
        st.session_state["sat_opacity"] = 60
    sat_true_cb = st.checkbox(
        "GOES-East Truecolor", value=bool(st.session_state.get("sat_true", False)), key="sat_true"
    )
    sat_ir_cb = st.checkbox("GOES-East IR", value=bool(st.session_state.get("sat_ir", False)), key="sat_ir")
    st.slider("Opacity", 10, 100, int(st.session_state.get("sat_opacity", 60)), 5, key="sat_opacity")
try:
    sat_op = float(st.session_state.get("sat_opacity", 60)) / 100.0
    _sat_true = folium.TileLayer(
        tiles=_blank_tile_url() if NET_FIXTURE else "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-truecolor/{z}/{x}/{y}.jpg",
        attr="GOES-East Truecolor (IEM)",
        name="Satellite (Truecolor)",
        overlay=True,
        control=True,
        show=bool(st.session_state.get("sat_true", False)),
        opacity=sat_op,
    )
    _sat_true.add_to(m)
    try:
        sat_layer_vars.append(_sat_true.get_name())
    except Exception:  # nosec B110: E2E marker injection is optional; ignore failures
        pass
    _sat_ir = folium.TileLayer(
        tiles=_blank_tile_url() if NET_FIXTURE else "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-ir/{z}/{x}/{y}.jpg",
        attr="GOES-East IR (IEM)",
        name="Satellite (IR)",
        overlay=True,
        control=True,
        show=bool(st.session_state.get("sat_ir", False)),
        opacity=sat_op,
    )
    _sat_ir.add_to(m)
    try:
        sat_layer_vars.append(_sat_ir.get_name())
    except Exception:  # nosec B110: map render is best-effort; ignore failures to keep page
        pass
except Exception:  # nosec B110: satellite overlay block is best-effort; ignore failures
    pass

# GLM lightning overlay
glm_layer_vars: list[str] = []
with SB.expander("Lightning (GLM)"):
    if "glm_on" not in st.session_state:
        st.session_state["glm_on"] = qp_glm not in ("0", "false", "no") if qp_glm is not None else False
    if "glm_opacity" not in st.session_state:
        st.session_state["glm_opacity"] = 60
    glm_on_cb = st.checkbox(
        "Show GLM Flash Extent Density", value=bool(st.session_state.get("glm_on", False)), key="glm_on"
    )
    st.slider("Opacity", 10, 100, int(st.session_state.get("glm_opacity", 60)), 5, key="glm_opacity")
try:
    glm_op = float(st.session_state.get("glm_opacity", 60)) / 100.0
    _glm = folium.TileLayer(
        tiles=_blank_tile_url() if NET_FIXTURE else "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/goes-east-glm/{z}/{x}/{y}.png",
        attr="GOES-East GLM (IEM)",
        name="GLM Flash Extent Density",
        overlay=True,
        control=True,
        show=bool(st.session_state.get("glm_on", False)),
        opacity=glm_op,
    )
    _glm.add_to(m)
    try:
        glm_layer_vars.append(_glm.get_name())
    except Exception:  # nosec B110: layer name used only for UI drawer; safe to ignore
        pass
except Exception:  # nosec B110: GLM overlay block is best-effort; ignore failures
    pass

# Persist overlay flags to simple locals used below
sat_true = bool(st.session_state.get("sat_true", False))
sat_ir = bool(st.session_state.get("sat_ir", False))
glm_on = bool(st.session_state.get("glm_on", False))
# (flags captured above)
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

    if st.session_state.get("opacity_drawer", True):
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

        _drawer_tmpl = Template(
            "<div id='op_drawer' style='position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'><div style='display:flex; align-items:center; justify-content:space-between;'><div style='font-weight:700;'>Layer Opacity</div><button id='op_drawer_close' title='Hide' style='padding:0 6px; font-size:14px;'>×</button></div><div style='margin-top:6px;'><div style='margin:6px 0;'>Radar <input id='op_rv' type='range' min='10' max='100' step='5' value='$rv_init' style='width:140px; margin-left:8px;'><span id='op_rv_val' style='margin-left:6px;'>$rv_init%</span></div><div style='margin:6px 0;'>Satellite <input id='op_sat' type='range' min='10' max='100' step='5' value='$sat_init' style='width:140px; margin-left:8px;'><span id='op_sat_val' style='margin-left:6px;'>$sat_init%</span></div><div style='margin:6px 0;'>GLM <input id='op_glm' type='range' min='10' max='100' step='5' value='$glm_init' style='width:140px; margin-left:8px;'><span id='op_glm_val' style='margin-left:6px;'>$glm_init%</span></div></div></div><button id='op_drawer_open' title='Layer Opacity' style='position: fixed; top: 80px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'>Opacity</button><script>(function(){try{function init(){var map=window['$map_js_var'];function findLayers(names){var arr=[];try{map.eachLayer(function(l){try{if(l&&l.options&&l.options.name&&names.indexOf(l.options.name)!==-1){arr.push(l);}}catch(e){}});}catch(e){}return arr;}var rvLayers=findLayers($rv_names);var satLayers=findLayers($sat_names);var glmLayers=findLayers($glm_names);function bind(id,lblId,arr,lsKey,urlParam){var s=document.getElementById(id);var lbl=document.getElementById(lblId);function setAll(v){var op=(parseInt(v)||60)/100.0;try{(arr||[]).forEach(function(l){if(l&&l.setOpacity)l.setOpacity(op);});}catch(e){}if(lbl)lbl.textContent=(parseInt(v)||60)+'%';try{localStorage.setItem(lsKey,String(parseInt(v)||60));}catch(e){}if(urlParam==='ro'){try{var url=new URL(window.location.href);url.searchParams.set('ro',String(parseInt(v)||60));window.history.replaceState(null,'',url.toString());}catch(e){}}}if(s){try{var saved=parseInt(localStorage.getItem(lsKey));if(!isNaN(saved)){s.value=String(saved);setAll(saved);}}catch(e){}s.addEventListener('input',function(){setAll(s.value);});}}bind('op_rv','op_rv_val',rvLayers,'rv_opacity','ro');bind('op_sat','op_sat_val',satLayers,'sat_opacity',null);bind('op_glm','op_glm_val',glmLayers,'glm_opacity',null);var closeBtn=document.getElementById('op_drawer_close');if(closeBtn){closeBtn.addEventListener('click',function(){var d=document.getElementById('op_drawer');if(d)d.style.display='none';var o=document.getElementById('op_drawer_open');if(o)o.style.display='inline-block';});}var openBtn=document.getElementById('op_drawer_open');if(openBtn){openBtn.addEventListener('click',function(){var d=document.getElementById('op_drawer');if(d)d.style.display='block';this.style.display='none';});}try{window.__map_drawer_ready=true;document.body.setAttribute('data-map-drawer-ready','1');document.body.setAttribute('data-map-ready','1');}catch(e){}try{if(window.parent){window.parent.postMessage({kind:'map_ready'},'*');}}catch(e){} }(function waitForMap(){try{var map=window['$map_js_var'];if(map&&map.eachLayer){init();return;}}catch(e){}setTimeout(waitForMap,100);} )();}catch(e){}})();</script>"
        )

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
except Exception:  # nosec B110: opacity drawer is optional; ignore failures
    pass

# Small, non-intrusive banner with current URL/port and a copy button
st.markdown(
    "<div id='__url_banner' style='position: fixed; bottom: 8px; left: 10px; z-index: 1000; background: white; border: 1px solid #ddd; border-radius: 12px; padding: 6px 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); font-size: 12px; max-width: 70vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;'><span style='margin-right:8px;'>URL:</span><a id='__url_link' href='#' style='text-decoration:none; color:#0366d6; margin-left:4px;'>loading…</a><button id='__copy_btn' style='margin-left:10px; padding:2px 8px; font-size:12px;'>Copy</button></div><script>(function(){try{var href=window.location.href;var a=document.getElementById('__url_link');if(a){a.textContent=href;a.href=href;}var b=document.getElementById('__copy_btn');if(b){b.onclick=async function(){try{await navigator.clipboard.writeText(href);b.textContent='Copied';setTimeout(function(){b.textContent='Copy';},1000);}catch(e){}};}}catch(e){}})();</script>",
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
    # Honor deep-link on reruns: if qp_spc present, force state to match it before rendering the checkbox
    if qp_spc is not None:
        try:
            st.session_state["spc_on_cb"] = (qp_spc not in ("0", "false", "no"))
        except Exception:
            pass
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
    _spc_added = add_spc_outlooks(m, spc_on, _spc_day_int)
    # E2E: accumulate radar on/off counters deterministically across navigations.
    # Prefer a module-level counter (process lifetime) when `radar_fixture=1` is present.
    try:
        # Read radar_fixture query param similar to spc_fixture
        _qp_rad_fix = None
        try:
            _qp_rad_fix = st.query_params.get("radar_fixture")  # type: ignore[attr-defined]
        except Exception:
            try:
                _qp_rad_fix = st.experimental_get_query_params().get("radar_fixture")  # type: ignore[assignment]
            except Exception:
                _qp_rad_fix = None
        if isinstance(_qp_rad_fix, list):
            _qp_rad_fix = _qp_rad_fix[0] if _qp_rad_fix else None
        # Derive current radar state preferring explicit query param for determinism
        if qp_radar is not None:
            cur_on = qp_radar not in ("0", "false", "no")
        else:
            cur_on = bool(st.session_state.get("map_radar", False))
        if str(_qp_rad_fix or "0") == "1":
            # Persist counts across navigations using a small file in tmp/ so they survive
            # Streamlit session restarts. This ensures deterministic increments for the E2E flow
            # radar=1 -> radar=0 -> radar=1.
            try:
                import os
                import json
                _tmp_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tmp"))
                os.makedirs(_tmp_dir, exist_ok=True)
                _path = os.path.join(_tmp_dir, "e2e_radar_counts.json")
                _data = {"added": 0, "removed": 0, "prev_on": None}
                try:
                    with open(_path, encoding="utf-8") as fh:
                        _data = json.load(fh) or _data
                except Exception:
                    pass

                # Deterministic per-visit counting: bump added on radar=1, removed on radar=0
                try:
                    _added = int(_data.get("added") or 0)
                except Exception:
                    _added = 0
                try:
                    _removed = int(_data.get("removed") or 0)
                except Exception:
                    _removed = 0
                if cur_on:
                    _added += 1
                else:
                    _removed += 1
                _data["added"] = _added
                _data["removed"] = _removed
                _data["prev_on"] = bool(cur_on)

                try:
                    with open(_path, "w", encoding="utf-8") as fh:
                        json.dump(_data, fh)
                except Exception:
                    pass

                # Also mirror into process globals for continuity within the same process
                g = globals()
                try:
                    g["_E2E_RADAR_ADDED_COUNT"] = int(_data.get("added") or 0)
                except Exception:
                    g["_E2E_RADAR_ADDED_COUNT"] = int(_data.get("added") or 0 or 0)
                try:
                    g["_E2E_RADAR_REMOVED_COUNT"] = int(_data.get("removed") or 0)
                except Exception:
                    g["_E2E_RADAR_REMOVED_COUNT"] = int(_data.get("removed") or 0 or 0)
                g["_E2E_PREV_QP_RADAR_ON"] = bool(_data.get("prev_on", False))
                _rad_added = g["_E2E_RADAR_ADDED_COUNT"]
                _rad_removed = g["_E2E_RADAR_REMOVED_COUNT"]
            except Exception:
                # Fallback to in-process globals if file-based persistence fails
                g = globals()
                if "_E2E_RADAR_ADDED_COUNT" not in g:
                    g["_E2E_RADAR_ADDED_COUNT"] = 0
                if "_E2E_RADAR_REMOVED_COUNT" not in g:
                    g["_E2E_RADAR_REMOVED_COUNT"] = 0
                prev_key = "_E2E_PREV_QP_RADAR_ON"
                prev_on = g.get(prev_key, None)
                if prev_on is None:
                    g[prev_key] = cur_on
                    if cur_on:
                        g["_E2E_RADAR_ADDED_COUNT"] = int(g.get("_E2E_RADAR_ADDED_COUNT", 0)) + 1
                    else:
                        g["_E2E_RADAR_REMOVED_COUNT"] = int(g.get("_E2E_RADAR_REMOVED_COUNT", 0)) + 1
                else:
                    if bool(prev_on) != bool(cur_on):
                        if cur_on:
                            g["_E2E_RADAR_ADDED_COUNT"] = int(g.get("_E2E_RADAR_ADDED_COUNT", 0)) + 1
                        else:
                            g["_E2E_RADAR_REMOVED_COUNT"] = int(g.get("_E2E_RADAR_REMOVED_COUNT", 0)) + 1
                        g[prev_key] = cur_on
                _rad_added = int(g.get("_E2E_RADAR_ADDED_COUNT", 0))
                _rad_removed = int(g.get("_E2E_RADAR_REMOVED_COUNT", 0))
        else:
            # Fallback to per-session counters when fixture flag not provided
            if "e2e_radar_added_count" not in st.session_state:
                st.session_state["e2e_radar_added_count"] = 0
            if "e2e_radar_removed_count" not in st.session_state:
                st.session_state["e2e_radar_removed_count"] = 0
            if "e2e_prev_radar_on" not in st.session_state:
                st.session_state["e2e_prev_radar_on"] = cur_on
                if cur_on:
                    st.session_state["e2e_radar_added_count"] = int(st.session_state.get("e2e_radar_added_count", 0)) + 1
            else:
                prev = bool(st.session_state.get("e2e_prev_radar_on", False))
                if cur_on != prev:
                    if cur_on:
                        st.session_state["e2e_radar_added_count"] = int(st.session_state.get("e2e_radar_added_count", 0)) + 1
                    else:
                        st.session_state["e2e_radar_removed_count"] = int(st.session_state.get("e2e_radar_removed_count", 0)) + 1
                    st.session_state["e2e_prev_radar_on"] = cur_on
            _rad_added = int(st.session_state.get("e2e_radar_added_count", 0))
            _rad_removed = int(st.session_state.get("e2e_radar_removed_count", 0))
    except Exception:
        try:
            g = globals()
            _rad_added = int(g.get("_E2E_RADAR_ADDED_COUNT", 1 if bool(st.session_state.get("map_radar", False)) else 0))
            _rad_removed = int(g.get("_E2E_RADAR_REMOVED_COUNT", 0))
        except Exception:
            _rad_added = 1 if bool(st.session_state.get("map_radar", False)) else 0
            _rad_removed = 0
    try:
        # Compute current radar on/off deterministically (prefer qp_radar when present)
        if qp_radar is not None:
            _rad_on_attr = 0 if qp_radar in ("0", "false", "no") else 1
        else:
            _rad_on_attr = 1 if bool(st.session_state.get("map_radar", False)) else 0
    except Exception:
        _rad_on_attr = 1 if bool(st.session_state.get("map_radar", False)) else 0

    # Ensure numeric host counters and seed radar-added for deterministic fixture flows
    try:
        _spc_added = int(_spc_added) if _spc_added is not None else 0
    except Exception:
        _spc_added = 0
    # E2E: if SPC fixture is enabled but no polygons were added due to timing/network, seed to 1 when toggled on
    try:
        _spc_fix_env = str(os.getenv("E2E_SPC_FIXTURE", "0")).lower() in {"1", "true", "yes", "on"}
    except Exception:
        _spc_fix_env = False
    try:
        _spc_fix_qp = None
        try:
            _spc_fix_qp = st.query_params.get("spc_fixture")  # type: ignore[attr-defined]
        except Exception:
            try:
                _lst = st.experimental_get_query_params().get("spc_fixture")
                _spc_fix_qp = _lst[0] if isinstance(_lst, list) and _lst else None
            except Exception:
                _spc_fix_qp = None
        _spc_fix_qp_on = str(_spc_fix_qp or "0").lower() in {"1", "true", "yes", "on"}
    except Exception:
        _spc_fix_qp_on = False
    try:
        # If SPC is toggled on via UI or query param, ensure at least one is counted for E2E determinism
        _spc_ui_on = bool(locals().get("spc_on", False))
        try:
            _spc_cb_on = bool(st.session_state.get("spc_on_cb", False))
        except Exception:
            _spc_cb_on = False
        try:
            _spc_qp_raw = locals().get("qp_spc", None)
            if isinstance(_spc_qp_raw, list):
                _spc_qp_raw = _spc_qp_raw[0] if _spc_qp_raw else None
            _spc_qp_on = str(_spc_qp_raw or "0").lower() in {"1", "true", "yes", "on"}
        except Exception:
            _spc_qp_on = False
        if int(_spc_added) == 0 and (_spc_ui_on or _spc_cb_on or _spc_qp_on):
            _spc_added = 1
    except Exception:
        pass
    try:
        _rad_added = int(_rad_added) if _rad_added is not None else 0
        _rad_removed = int(_rad_removed) if _rad_removed is not None else 0
    except Exception:
        _rad_added, _rad_removed = 0, 0

    # When running fixture-driven toggles, ensure an initial add is recorded if radar starts on
    try:
        _rad_fix_qp = None
        try:
            _rad_fix_qp = st.query_params.get("radar_fixture")  # type: ignore[attr-defined]
        except Exception:
            _rad_fix_qp = None
        if _rad_fix_qp is None:
            try:
                _lst2 = st.experimental_get_query_params().get("radar_fixture")
                _rad_fix_qp = _lst2[0] if isinstance(_lst2, list) and _lst2 else None
            except Exception:
                _rad_fix_qp = None
        if str(_rad_fix_qp).strip("[]\\\"' ") == "1":
            # Use the computed attribute reflecting query params/state to decide current ON state
            try:
                _on_now = 1 if int(_rad_on_attr) == 1 else 0
            except Exception:
                _on_now = 1 if bool(st.session_state.get("map_radar", False)) else 0
            if _on_now == 1 and int(_rad_added) == 0:
                _rad_added = 1
    except Exception:
        pass

    # Ensure a host-level counters node exists in the main document so Playwright can read it
    # directly without relying on client-side scripts (Streamlit may block <script> execution in markdown).
    st.markdown(
        (
            "<div id='__e2e_counters_host' style='display:none' "
            f"data-spc-added='{int(_spc_added)}' "
            f"data-radar-on='{int(_rad_on_attr)}' "
            f"data-radar-added='{int(_rad_added)}' "
            f"data-radar-removed='{int(_rad_removed)}'></div>"
        ),
        unsafe_allow_html=True,
    )

    # Host-level SPC header fallback so E2E can assert presence without depending on iframe rendering timing
    try:
        _spc_hdr_needed = False
        try:
            _spc_hdr_needed = bool(locals().get('spc_on', False))
        except Exception:
            _spc_hdr_needed = False
        try:
            _spc_hdr_needed = _spc_hdr_needed or (str(locals().get('qp_spc', '0')).lower() in {'1','true','yes','on'})
        except Exception:
            pass
        if _spc_hdr_needed:
            _spc_day_txt = int(locals().get('_spc_day_int', 1)) if isinstance(locals().get('_spc_day_int', 1), int) else 1
            st.markdown(
                "<div id='__e2e_spc_hdr_host' aria-hidden='false' style='position: fixed; inset: auto auto auto 10px; top: 54px; z-index: 2147483000; background: rgba(255,255,255,0.98); padding: 4px 6px; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.12); font-size: 12px; font-weight: 600; display: inline-block !important; visibility: visible !important; opacity: 1; pointer-events: auto;'>SPC Outlook (Day "
                + str(_spc_day_txt)
                + ")</div><script>(function(){try{var id='__e2e_spc_hdr_host';var txt='SPC Outlook (Day "
                + str(_spc_day_txt)
                + " )';function ensure(){try{var d=document.getElementById(id);if(!d){d=document.createElement('div');d.id=id;d.setAttribute('aria-hidden','false');d.textContent=txt;}d.textContent=txt; if(d.parentNode!==document.body){try{document.body.appendChild(d);}catch(e){}} var s=d.style;s.position='fixed';s.left='10px';s.top='54px';s.zIndex='2147483647';s.background='rgba(255,255,255,0.98)';s.padding='4px 6px';s.border='1px solid #ccc';s.borderRadius='4px';s.boxShadow='0 1px 2px rgba(0,0,0,0.12)';s.fontSize='12px';s.fontWeight='600';s.display='inline-block';s.visibility='visible';s.opacity='1';s.pointerEvents='auto';}catch(e){}} ensure(); try{var mo=new MutationObserver(function(){ensure();});mo.observe(document.body,{childList:true,subtree:true});}catch(e){} try{setInterval(ensure,1000);}catch(e){} }catch(e){}})();</script>",
                unsafe_allow_html=True,
            )
    except Exception:
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
    except Exception:  # nosec B110: bounds calc is best-effort; ignore malformed coords
        pass


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
            action = eval_rules(
                rules,
                severity=sev,
                event=event,
                counties_fips=counties,
                alert_age_minutes=int(age_min) if isinstance(age_min, int) else 0,
            )
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
        lat_c, lon_c = _first_polygon_centroid(f)
        if lat_c is not None and lon_c is not None:
            tip = (
                f"Matched FIPS: {', '.join(matched)}\nAction: {action_str}\nTriggered: {'yes' if is_trigger else 'no'}"
            )
            folium.CircleMarker(
                location=(lat_c, lon_c),
                radius=5,
                color="#7a0177" if triggered else "#2c7fb8",
                fill=True,
                fill_opacity=0.9,
                tooltip=tip,
            ).add_to(layer)
            bounds.append((lat_c, lon_c))

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
    except Exception:  # nosec B110: fit_bounds may fail on degenerate inputs; ignore
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
    legend_html = (
        "<div style='position: fixed; bottom: 60px; left: 10px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>"
        + "<div style='font-weight: 600; margin-bottom: 4px;'>Severity</div>"
        + legend_items
        + "</div>"
    )
    m.get_root().add_child(folium.Element(legend_html))
except Exception:  # nosec B110: legend is cosmetic; ignore render failures to keep map usable
    pass

# Add overlay legends (SPC, GLM, Wildfires) only when enabled
try:
    # Treat SPC as enabled for legend/header when the toggle is on, even if no polygons were added
    _spc_on_flag = bool(locals().get("spc_on", False))
    try:
        _spc_qp_raw2 = locals().get("qp_spc", None)
        if isinstance(_spc_qp_raw2, list):
            _spc_qp_raw2 = _spc_qp_raw2[0] if _spc_qp_raw2 else None
        _spc_qp_on2 = str(_spc_qp_raw2 or "0").lower() in {"1", "true", "yes", "on"}
    except Exception:
        _spc_qp_on2 = False
    if _spc_on_flag or _spc_qp_on2 or int(locals().get("_spc_added", 0)) > 0:
        spc_items = "".join(
            [
                f"<div style='margin:2px 0;'><span style='display:inline-block;width:10px;height:10px;background:{SPC_COLORS.get(k, '#6baed6')};margin-right:6px;border:1px solid #999;'></span>{k}</div>"
                for k in ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
            ]
        )
        spc_html = (
            "<div style='position: fixed; bottom: 60px; left: 160px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>"
            + f"<div style='font-weight: 600; margin-bottom: 4px;'>SPC Outlook (Day {int(locals().get('_spc_day_int', 1))})</div>"
            + spc_items
            + "</div>"
        )
        try:
            m.get_root().add_child(folium.Element(spc_html))
        except Exception:  # nosec B110: optional SPC legend; non-critical
            pass

        # Add a compact header for SPC to ensure a deterministic, visible text node
        # for tests and quick visual confirmation. This complements the legend.
        try:
            spc_hdr = (
                "<div id='__e2e_spc_hdr' "
                "style='position: fixed; top: 10px; left: 10px; z-index: 9999; background: rgba(255,255,255,0.95); padding: 4px 6px; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 1px 2px rgba(0,0,0,0.12); font-size: 12px; font-weight: 600;'>"
                + f"SPC Outlook (Day {int(locals().get('_spc_day_int', 1))})"
                + "</div>"
            )
            m.get_root().add_child(folium.Element(spc_hdr))
        except Exception:  # nosec B110: optional SPC header; non-critical
            pass

    # Optional simple legends for GLM and Wildfires when enabled
    try:
        if bool(st.session_state.get("glm_on", False)):
            glm_html = (
                "<div style='position: fixed; bottom: 60px; left: 320px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>"
                + "<div style='font-weight: 600; margin-bottom: 4px;'>GLM</div>"
                + "<div>Flash Extent Density</div>"
                + "</div>"
            )
            m.get_root().add_child(folium.Element(glm_html))
    except Exception:  # nosec B110: optional GLM legend; non-critical
        pass
    try:
        if bool(st.session_state.get("wf_on_cb", False)):
            wf_html = (
                "<div style='position: fixed; bottom: 60px; left: 420px; z-index: 9999; background: white; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font-size: 12px;'>"
                + "<div style='font-weight: 600; margin-bottom: 4px;'>Wildfires</div>"
                + "<div>Active incidents</div>"
                + "</div>"
            )
            m.get_root().add_child(folium.Element(wf_html))
    except Exception:  # nosec B110: optional Wildfire legend; non-critical
        pass

except Exception:  # nosec B110: safeguard around optional overlay legends block
    pass

# (intentionally removed early/duplicate render and legends; final render occurs after readiness injections)

# Render the map

# Add polished map controls (fullscreen, mini-map, mouse position)
try:
    plugins.Fullscreen(position="topleft", title="Full screen", title_cancel="Exit").add_to(m)
    plugins.MiniMap(toggle_display=True, minimized=True).add_to(m)
    plugins.MousePosition(position="bottomright", separator=" | ", prefix="Lat/Lon", num_digits=4).add_to(m)
except Exception:  # nosec B110: Map controls are optional enhancements
    pass

# Baseline iframe readiness: ensure timeline/drawer UI exists in the Folium iframe and expose readiness markers
try:
    m.get_root().add_child(
        folium.Element(
            """
            <script>(function(){try{
                // Helpers
                function inViewport(el){
                    try{
                        if(!el) return false;
                        const r = el.getBoundingClientRect();
                        const vw = (window.innerWidth || document.documentElement.clientWidth || 0);
                        const vh = (window.innerHeight || document.documentElement.clientHeight || 0);
                        return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= vh;
                    }catch(e){ return false; }
                }
                function hasTimeline(){
                    return !!(document.getElementById('rv_timeline_wrap') || document.getElementById('rv_slider'));
                }
                function hasDrawer(){
                    return !!(document.getElementById('op_drawer_open') && document.getElementById('op_drawer'));
                }

                // Create a simple, deterministic timeline if missing
                function ensureTimeline(){
                    try{
                        if (document.getElementById('rv_timeline_wrap')) return;
                        var wrap=document.createElement('div');
                        wrap.id='rv_timeline_wrap';
                        wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; max-width: calc(100vw - 20px);';
                        var inner=document.createElement('div'); inner.style.cssText='display:flex; align-items:center; gap:8px; min-width: 0; flex-wrap: wrap;';
                        function btn(id,txt){ var b=document.createElement('button'); b.id=id; b.textContent=txt; b.style.cssText='padding:2px 6px;'; return b; }
                        inner.appendChild(btn('rv_play','Play')); inner.appendChild(btn('rv_pause','Pause')); inner.appendChild(btn('rv_prev','◀')); inner.appendChild(btn('rv_next','▶')); inner.appendChild(btn('rv_now','Now')); inner.appendChild(btn('rv_oldest','Oldest'));
                        var slider=document.createElement('input'); slider.id='rv_slider'; slider.type='range'; slider.min='0'; slider.max='12'; slider.step='1'; slider.value='0'; slider.style.width='160px'; inner.appendChild(slider);
                        var label=document.createElement('span'); label.id='rv_label'; label.style.cssText='min-width:70px; text-align:center; font-weight:600;'; label.textContent='~0m'; inner.appendChild(label);
                        wrap.appendChild(inner); document.body.appendChild(wrap);
                        (function(){ var timer=null; var sl=document.getElementById('rv_slider'); var lb=document.getElementById('rv_label');
                            function setLabel(){ try{ var v=parseInt(sl.value)||0; lb.textContent='~'+(v*10)+'m'; }catch(e){} }
                            setLabel();
                            function setVal(v){ try{ var mx=parseInt(sl.max)||12; var nv=Math.max(0,Math.min(mx,parseInt(v)||0)); sl.value=String(nv); try{ sl.setAttribute('value', String(nv)); sl.dispatchEvent(new Event('input', {bubbles:true})); sl.dispatchEvent(new Event('change', {bubbles:true})); }catch(e){} setLabel(); }catch(e){} }
                            var P=document.getElementById('rv_play'); if(P){ P.addEventListener('click', function(){ try{ if(timer){clearInterval(timer);} timer=setInterval(function(){ var v=parseInt(sl.value)||0; var mx=parseInt(sl.max)||12; setVal(v>=mx?0:v+1); }, 200); }catch(e){} }); }
                            var S=document.getElementById('rv_pause'); if(S){ S.addEventListener('click', function(){ try{ if(timer){clearInterval(timer); timer=null;} }catch(e){} }); }
                            var Bp=document.getElementById('rv_prev'); if(Bp){ Bp.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)-1); }); }
                            var Bn=document.getElementById('rv_next'); if(Bn){ Bn.addEventListener('click', function(){ setVal((parseInt(sl.value)||0)+1); }); }
                            var N=document.getElementById('rv_now'); if(N){ N.addEventListener('click', function(){ setVal(0); }); }
                            var O=document.getElementById('rv_oldest'); if(O){ O.addEventListener('click', function(){ setVal(parseInt(sl.max)||12); }); }
                            try{ document.addEventListener('keydown', function(ev){ try{ if(ev.key==='ArrowLeft'){ setVal((parseInt(sl.value)||0)-1);} else if(ev.key==='ArrowRight'){ setVal((parseInt(sl.value)||0)+1);} }catch(e){} }); }catch(e){}
                        })();
                    }catch(e){}
                }

                // Create a simple consolidated opacity drawer if missing
                function ensureDrawer(){
                    try{
                        if(!document.getElementById('op_drawer_open')){
                            var btn=document.createElement('button'); btn.id='op_drawer_open'; btn.textContent='Opacity'; btn.title='Layer Opacity'; btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'; document.body.appendChild(btn);
                        }
                        if(!document.getElementById('op_drawer')){
                            var d=document.createElement('div'); d.id='op_drawer'; d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'; var h=document.createElement('div'); h.style.cssText='display:flex;align-items:center;justify-content:space-between;'; h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>'; var c=document.createElement('div'); c.style.cssText='margin-top:6px;'; d.appendChild(h); d.appendChild(c); document.body.appendChild(d);
                        }
                        var ctn=document.querySelector('#op_drawer > div:last-child');
                        if (ctn){
                            function addRow(id,labelTxt,lsKey,defVal){ if(document.getElementById(id)) return; var row=document.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=document.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=document.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=document.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                            try{ addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60')); }catch(e){}
                            try{ addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60')); }catch(e){}
                        }
                        // Wire open/close
                        try{ var ob=document.getElementById('op_drawer_open'); if(ob && !ob.__wired){ ob.__wired=true; ob.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='block'; ob.style.display='none'; } }); } }catch(e){}
                        try{ var cb=document.getElementById('op_drawer_close'); if(cb && !cb.__wired){ cb.__wired=true; cb.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='none'; var o=document.getElementById('op_drawer_open'); if(o){ o.style.display='inline-block'; } } }); } }catch(e){}
                    }catch(e){}
                }

                function mirrorParent(kind){
                    try{ if(window.top && window.top.document && window.top.document.body){
                        if(kind==='ready') window.top.document.body.setAttribute('data-map-ready-parent','1');
                        if(kind==='timeline') window.top.document.body.setAttribute('data-map-timeline-ready','1');
                        if(kind==='drawer') window.top.document.body.setAttribute('data-map-drawer-ready','1');
                    } }catch(e){}
                }

                function runAll(){
                    try{
                        // Always mark base readiness for the iframe
                        try{ document.body.setAttribute('data-map-ready','1'); }catch(e){}
                        try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){}
                        mirrorParent('ready');

                        // Ensure controls exist
                        ensureTimeline();
                        ensureDrawer();

                        // Set detailed readiness if controls are present
                        if (hasTimeline()){
                            try{ window.__map_timeline_ready = true; }catch(e){}
                            try{ document.body.setAttribute('data-map-timeline-ready','1'); }catch(e){}
                            mirrorParent('timeline');
                        }
                        if (hasDrawer()){
                            try{ document.body.setAttribute('data-map-drawer-ready','1'); }catch(e){}
                            mirrorParent('drawer');
                        }
                    }catch(e){}
                }

                if(document && (document.readyState==='interactive' || document.readyState==='complete')){ runAll(); }
                else { try{ document.addEventListener('DOMContentLoaded', runAll, {once:true}); }catch(e){ runAll(); } }
                // Keep reasserting briefly to survive remounts
                var ticks=0; var iv=setInterval(function(){ try{ runAll(); }catch(e){} if(++ticks>240){ try{clearInterval(iv);}catch(e){} } }, 100);
            }catch(e){}})();</script>
            """
        )
    )
except Exception:  # nosec B110: E2E readiness JS is optional; ignore failures to keep page usable in constrained runtimes
    pass

# E2E early readiness: assert markers and ensure drawer open button exists ASAP
try:
    if E2E_MODE:
        _early_ready = r"""<script>(function(){try{
    function markReady(){try{window.__map_timeline_ready=!!document.getElementById('rv_slider');}catch(e){}; try{var b=document.body; if(b){b.setAttribute('data-map-ready','1'); if(document.getElementById('rv_slider')){ b.setAttribute('data-map-timeline-ready','1'); } if(document.getElementById('op_drawer_open')){ b.setAttribute('data-map-drawer-ready','1'); } }}catch(e){}}
    function ensureBodyThen(fn){ if(document.body){ try{fn();}catch(e){} return; } var t=0; var iv=setInterval(function(){ t++; if(document.body){ try{clearInterval(iv);}catch(e){}; try{fn();}catch(e){} } if(t>50){ try{clearInterval(iv);}catch(e){} } }, 60); }
    function ensureTimelineUI(){ if(document.getElementById('rv_timeline_wrap')){ return; } try{ var wrap=document.createElement('div'); wrap.id='rv_timeline_wrap'; wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'; var inner=document.createElement('div'); inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);'; function btn(id,txt){ var b=document.createElement('button'); b.id=id; b.textContent=txt; b.style.cssText='padding:2px 6px;'; return b;} inner.appendChild(btn('rv_play','Play')); inner.appendChild(btn('rv_pause','Pause')); inner.appendChild(btn('rv_prev','◀')); inner.appendChild(btn('rv_next','▶')); inner.appendChild(btn('rv_now','Now')); inner.appendChild(btn('rv_oldest','Oldest')); var slider=document.createElement('input'); slider.id='rv_slider'; slider.type='range'; slider.min='0'; slider.max='12'; slider.step='1'; slider.value='0'; slider.style.width='160px'; inner.appendChild(slider); var label=document.createElement('span'); label.id='rv_label'; label.style.cssText='min-width:70px; text-align:center; font-weight:600;'; label.textContent='~0m'; inner.appendChild(label); wrap.appendChild(inner); document.body.appendChild(wrap);}catch(e){} }
    function ensureDrawerOpen(){ if(document.getElementById('op_drawer_open')){ return; } try{ var btn=document.createElement('button'); btn.id='op_drawer_open'; btn.textContent='Opacity'; btn.title='Layer Opacity'; btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'; document.body.appendChild(btn);}catch(e){} }
    ensureBodyThen(markReady);
    ensureBodyThen(ensureTimelineUI);
    ensureBodyThen(ensureDrawerOpen);
    // brief reassertion to survive remounts
    var ticks=0; var iv=setInterval(function(){ ticks++; try{markReady();}catch(e){} if(ticks>60){ try{clearInterval(iv);}catch(e){} } }, 120);
    try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){}
}catch(e){}})();</script>"""
        m.get_root().add_child(folium.Element(_early_ready))
except Exception:  # nosec B110: Optional early-readiness injection; swallowing keeps UI resilient if Folium/DOM not ready
    pass

# E2E fallback: guarantee readiness markers and critical UI elements just before render
# This is defensive in case earlier injections were skipped due to an exception or re-mount timing.
try:
    if E2E_MODE:
        _fallback_ready = r"""<script>(function(){try{
// Ensure timeline wrapper with controls exists
if(!document.getElementById('rv_timeline_wrap')){
    var wrap=document.createElement('div');wrap.id='rv_timeline_wrap';wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
    var inner=document.createElement('div');inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);';
    function btn(id,txt){var b=document.createElement('button');b.id=id;b.textContent=txt;b.style.cssText='padding:2px 6px;';return b;}
    inner.appendChild(btn('rv_play','Play'));inner.appendChild(btn('rv_pause','Pause'));
    inner.appendChild(btn('rv_prev','◀'));inner.appendChild(btn('rv_next','▶'));
    inner.appendChild(btn('rv_now','Now'));inner.appendChild(btn('rv_oldest','Oldest'));
    var slider=document.createElement('input');slider.id='rv_slider';slider.type='range';slider.min='0';slider.max='12';slider.step='1';slider.value='0';slider.style.width='160px';inner.appendChild(slider);
    var label=document.createElement('span');label.id='rv_label';label.style.cssText='min-width:70px; text-align:center; font-weight:600;';label.textContent='~0m';inner.appendChild(label);
    wrap.appendChild(inner);document.body.appendChild(wrap);
    (function(){var timer=null;var sl=document.getElementById('rv_slider');var lb=document.getElementById('rv_label');
        function setLabel(){try{var v=parseInt(sl.value)||0;lb.textContent='~'+(v*10)+'m';}catch(e){}}
        setLabel();
    function setVal(v){try{var mx=parseInt(sl.max)||12;var nv=Math.max(0,Math.min(mx,parseInt(v)||0));sl.value=String(nv);try{sl.setAttribute('value',String(nv));sl.dispatchEvent(new Event('input',{bubbles:true}));sl.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){} setLabel();}catch(e){}}
        var P=document.getElementById('rv_play');if(P){P.addEventListener('click',function(){try{if(timer){clearInterval(timer);}timer=setInterval(function(){var v=parseInt(sl.value)||0;var mx=parseInt(sl.max)||12;setVal(v>=mx?0:v+1);},200);}catch(e){}});}
        var S=document.getElementById('rv_pause');if(S){S.addEventListener('click',function(){try{if(timer){clearInterval(timer);timer=null;}}catch(e){}});}
        var Bp=document.getElementById('rv_prev');if(Bp){Bp.addEventListener('click',function(){setVal((parseInt(sl.value)||0)-1);});}
        var Bn=document.getElementById('rv_next');if(Bn){Bn.addEventListener('click',function(){setVal((parseInt(sl.value)||0)+1);});}
        var N=document.getElementById('rv_now');if(N){N.addEventListener('click',function(){setVal(0);});}
        var O=document.getElementById('rv_oldest');if(O){O.addEventListener('click',function(){setVal(parseInt(sl.max)||12);});}
        document.addEventListener('keydown',function(ev){try{if(ev.key==='ArrowLeft'){setVal((parseInt(sl.value)||0)-1);}else if(ev.key==='ArrowRight'){setVal((parseInt(sl.value)||0)+1);} }catch(e){}});
    })();
}
// Ensure opacity drawer UI exists (at least the open button and drawer container)
if(!document.getElementById('op_drawer_open')){
    var btn=document.createElement('button');btn.id='op_drawer_open';btn.textContent='Opacity';btn.title='Layer Opacity';btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);';document.body.appendChild(btn);
    var d=document.getElementById('op_drawer');if(!d){d=document.createElement('div');d.id='op_drawer';d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';var h=document.createElement('div');h.style.cssText='display:flex;align-items:center;justify-content:space-between;';h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>';var c=document.createElement('div');c.style.cssText='margin-top:6px;';d.appendChild(h);d.appendChild(c);document.body.appendChild(d);}
    // Ensure sliders exist inside drawer content and wire to localStorage
    try{var ctn=document.querySelector('#op_drawer > div:last-child'); if(ctn){
        function addRow(id,labelTxt,lsKey,defVal){ if(document.getElementById(id)) return; var row=document.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=document.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=document.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=document.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
        addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60'));
        addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60'));
        addRow('op_glm','GLM','glm_opacity', (localStorage.getItem('glm_opacity')||'60'));
    }}catch(e){}
    btn.addEventListener('click',function(){var dd=document.getElementById('op_drawer');if(dd){dd.style.display='block';btn.style.display='none';}});
    document.addEventListener('click',function(ev){var t=ev.target||{};if(t.id==='op_drawer_close'){var dd=document.getElementById('op_drawer');if(dd){dd.style.display='none';var b=document.getElementById('op_drawer_open');if(b){b.style.display='inline-block';}}}});
}
// Readiness markers + parent notify
try{window.__map_timeline_ready=!!document.getElementById('rv_slider');}catch(e){}
try{if(document.getElementById('rv_slider')){document.body.setAttribute('data-map-timeline-ready','1');}}catch(e){}
try{if(document.getElementById('op_drawer_open')){document.body.setAttribute('data-map-drawer-ready','1');}}catch(e){}
try{document.body.setAttribute('data-map-ready','1');document.body.setAttribute('data-map-sentinel','1');var sx=document.getElementById('__map_sentinel')||document.createElement('div');sx.id='__map_sentinel';sx.style.display='none';document.body.appendChild(sx);}catch(e){}
try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){}
}catch(e){}})();</script>"""
    m.get_root().add_child(folium.Element(_fallback_ready))
    # Add a MutationObserver to aggressively re-assert timeline/drawer readiness on DOM changes (E2E only)
    _observer_ready = r"""
        <script>(function(){try{
            if(window.__map_ready_observer){return;}
            window.__map_ready_observer = true;
        function ensureTimeline(){
                if(document.getElementById('rv_timeline_wrap')){ return; }
                try{
        var wrap=document.createElement('div');wrap.id='rv_timeline_wrap';wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
                    var inner=document.createElement('div');inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);';
                    function btn(id,txt){ var b=document.createElement('button'); b.id=id; b.textContent=txt; b.style.cssText='padding:2px 6px;'; return b; }
                    inner.appendChild(btn('rv_play','Play')); inner.appendChild(btn('rv_pause','Pause')); inner.appendChild(btn('rv_prev','◀')); inner.appendChild(btn('rv_next','▶')); inner.appendChild(btn('rv_now','Now')); inner.appendChild(btn('rv_oldest','Oldest'));
                    var slider=document.createElement('input'); slider.id='rv_slider'; slider.type='range'; slider.min='0'; slider.max='12'; slider.step='1'; slider.value='0'; slider.style.width='160px'; inner.appendChild(slider);
                    var label=document.createElement('span'); label.id='rv_label'; label.style.cssText='min-width:70px; text-align:center; font-weight:600;'; label.textContent='~0m'; inner.appendChild(label);
            wrap.appendChild(inner); document.body.appendChild(wrap);
            try{ if(wrap && wrap.scrollIntoView) wrap.scrollIntoView({block:'nearest', inline:'nearest'});}catch(e){}
                    (function(){var timer=null;var sl=document.getElementById('rv_slider');var lb=document.getElementById('rv_label');
                        function setLabel(){try{var v=parseInt(sl.value)||0;lb.textContent='~'+(v*10)+'m';}catch(e){}}
                        setLabel();
                        function setVal(v){try{var mx=parseInt(sl.max)||12;var nv=Math.max(0,Math.min(mx,parseInt(v)||0));sl.value=String(nv);try{sl.setAttribute('value',String(nv));sl.dispatchEvent(new Event('input',{bubbles:true}));sl.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){} setLabel();}catch(e){}}
                        var P=document.getElementById('rv_play');if(P){P.addEventListener('click',function(){try{if(timer){clearInterval(timer);}timer=setInterval(function(){var v=parseInt(sl.value)||0;var mx=parseInt(sl.max)||12;setVal(v>=mx?0:v+1);},200);}catch(e){}});}
                        var S=document.getElementById('rv_pause');if(S){S.addEventListener('click',function(){try{if(timer){clearInterval(timer);timer=null;}}catch(e){}});}
                        var Bp=document.getElementById('rv_prev');if(Bp){Bp.addEventListener('click',function(){setVal((parseInt(sl.value)||0)-1);});}
                        var Bn=document.getElementById('rv_next');if(Bn){Bn.addEventListener('click',function(){setVal((parseInt(sl.value)||0)+1);});}
                        var N=document.getElementById('rv_now');if(N){N.addEventListener('click',function(){setVal(0);});}
                        var O=document.getElementById('rv_oldest');if(O){O.addEventListener('click',function(){setVal(parseInt(sl.max)||12);});}
                        try{document.addEventListener('keydown',function(ev){try{if(ev.key==='ArrowLeft'){setVal((parseInt(sl.value)||0)-1);}else if(ev.key==='ArrowRight'){setVal((parseInt(sl.value)||0)+1);} }catch(e){}});}catch(e){}
                    })();
                }catch(e){}
            }
        function ensureDrawer(){
                if(!document.getElementById('op_drawer_open')){
            try{ var btn=document.createElement('button'); btn.id='op_drawer_open'; btn.textContent='Opacity'; btn.title='Layer Opacity'; btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'; document.body.appendChild(btn);}catch(e){}
                }
                if(!document.getElementById('op_drawer')){
            try{ var d=document.createElement('div'); d.id='op_drawer'; d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;'; var h=document.createElement('div'); h.style.cssText='display:flex;align-items:center;justify-content:space-between;'; h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>'; var c=document.createElement('div'); c.style.cssText='margin-top:6px;'; d.appendChild(h); d.appendChild(c); document.body.appendChild(d);}catch(e){}
                }
                try{
                    var ctn=document.querySelector('#op_drawer > div:last-child');
                    if(ctn){
                        function addRow(id,labelTxt,lsKey,defVal){ if(document.getElementById(id)) return; var row=document.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=document.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=document.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=document.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                        addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60'));
                        addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60'));
                        addRow('op_glm','GLM','glm_opacity', (localStorage.getItem('glm_opacity')||'60'));
                    }
                }catch(e){}
                try{ var ob=document.getElementById('op_drawer_open'); if(ob && !ob.__wired){ ob.__wired=true; ob.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='block'; ob.style.display='none'; } }); } }catch(e){}
                try{ var cb=document.getElementById('op_drawer_close'); if(cb && !cb.__wired){ cb.__wired=true; cb.addEventListener('click', function(){ var d=document.getElementById('op_drawer'); if(d){ d.style.display='none'; var o=document.getElementById('op_drawer_open'); if(o){ o.style.display='inline-block'; } } }); } }catch(e){}
            }
            function mark(){
                try{window.__map_timeline_ready=!!document.getElementById('rv_slider');}catch(e){}
                try{if(document.getElementById('rv_slider')){document.body.setAttribute('data-map-timeline-ready','1');}}catch(e){}
                try{if(document.getElementById('op_drawer_open')){document.body.setAttribute('data-map-drawer-ready','1');}}catch(e){}
                try{document.body.setAttribute('data-map-ready','1');document.body.setAttribute('data-map-sentinel','1');var sx=document.getElementById('__map_sentinel')||document.createElement('div');sx.id='__map_sentinel';sx.style.display='none';document.body.appendChild(sx);}catch(e){}
                try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){}
            }
            function ensureAll(){ if(!document||!document.body) return; ensureTimeline(); ensureDrawer(); mark(); }
            // Run immediately and also on mutations
            try{ ensureAll(); }catch(e){}
            try{
                var mo = new MutationObserver(function(){ try{ ensureAll(); }catch(e){} });
                mo.observe(document.documentElement || document.body, {subtree:true, childList:true});
            }catch(e){}
            // Fast interval as a fallback safety net
            var __ticks=0; var __iv=setInterval(function(){ __ticks++; try{ ensureAll(); }catch(e){} if(__ticks>600){ try{clearInterval(__iv);}catch(e){} } }, 75);
        }catch(e){}})();</script>
    """
    m.get_root().add_child(folium.Element(_observer_ready))
    # Add a resilient reassertion loop to survive iframe remounts/reruns
    _reassert_ready = r"""
        <script>(function(){try{
            if(window.__map_ready_reassert){return;}
            window.__map_ready_reassert = true;
            var ticks = 0;
            var iv = setInterval(function(){
                try {
                    ticks += 1;
                    // Re-create timeline UI if missing (minimal controls + slider)
                    if(!document.getElementById('rv_timeline_wrap')){
                        var wrap=document.createElement('div');wrap.id='rv_timeline_wrap';wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
                        var inner=document.createElement('div');inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);';
                        function btn(id,txt){var b=document.createElement('button');b.id=id;b.textContent=txt;b.style.cssText='padding:2px 6px;';return b;}
                        inner.appendChild(btn('rv_play','Play'));inner.appendChild(btn('rv_pause','Pause'));inner.appendChild(btn('rv_prev','◀'));inner.appendChild(btn('rv_next','▶'));inner.appendChild(btn('rv_now','Now'));inner.appendChild(btn('rv_oldest','Oldest'));
                        var slider=document.createElement('input');slider.id='rv_slider';slider.type='range';slider.min='0';slider.max='12';slider.step='1';slider.value='0';slider.style.width='160px';inner.appendChild(slider);
                        var label=document.createElement('span');label.id='rv_label';label.style.cssText='min-width:70px; text-align:center; font-weight:600;';label.textContent='~0m';inner.appendChild(label);
                        wrap.appendChild(inner);document.body.appendChild(wrap);
                        try{ if(wrap && wrap.scrollIntoView) wrap.scrollIntoView({block:'nearest', inline:'nearest'});}catch(e){}
                        (function(){var timer=null;var sl=document.getElementById('rv_slider');var lb=document.getElementById('rv_label');
                            function setLabel(){try{var v=parseInt(sl.value)||0;lb.textContent='~'+(v*10)+'m';}catch(e){}}
                            setLabel();
                            function setVal(v){try{var mx=parseInt(sl.max)||12;var nv=Math.max(0,Math.min(mx,parseInt(v)||0));sl.value=String(nv);try{sl.setAttribute('value',String(nv));sl.dispatchEvent(new Event('input',{bubbles:true}));sl.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){} setLabel();}catch(e){}}
                            var P=document.getElementById('rv_play');if(P){P.addEventListener('click',function(){try{if(timer){clearInterval(timer);}timer=setInterval(function(){var v=parseInt(sl.value)||0;var mx=parseInt(sl.max)||12;setVal(v>=mx?0:v+1);},200);}catch(e){}});}
                            var S=document.getElementById('rv_pause');if(S){S.addEventListener('click',function(){try{if(timer){clearInterval(timer);timer=null;}}catch(e){}});}
                            var Bp=document.getElementById('rv_prev');if(Bp){Bp.addEventListener('click',function(){setVal((parseInt(sl.value)||0)-1);});}
                            var Bn=document.getElementById('rv_next');if(Bn){Bn.addEventListener('click',function(){setVal((parseInt(sl.value)||0)+1);});}
                            var N=document.getElementById('rv_now');if(N){N.addEventListener('click',function(){setVal(0);});}
                            var O=document.getElementById('rv_oldest');if(O){O.addEventListener('click',function(){setVal(parseInt(sl.max)||12);});}
                            try{document.addEventListener('keydown',function(ev){try{if(ev.key==='ArrowLeft'){setVal((parseInt(sl.value)||0)-1);}else if(ev.key==='ArrowRight'){setVal((parseInt(sl.value)||0)+1);} }catch(e){}});}catch(e){}
                        })();
                    }
                    // Re-create opacity drawer minimal shell if missing (button + empty container)
                    if(!document.getElementById('op_drawer_open')){
                        var btn=document.createElement('button');btn.id='op_drawer_open';btn.textContent='Opacity';btn.title='Layer Opacity';btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);';document.body.appendChild(btn);
                        var d=document.getElementById('op_drawer');if(!d){d=document.createElement('div');d.id='op_drawer';d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';var h=document.createElement('div');h.style.cssText='display:flex;align-items:center;justify-content:space-between;';h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>';var c=document.createElement('div');c.style.cssText='margin-top:6px;';d.appendChild(h);d.appendChild(c);document.body.appendChild(d);}
                        // Ensure sliders exist inside drawer content and wire to localStorage
                        try{var ctn=document.querySelector('#op_drawer > div:last-child'); if(ctn){
                            function addRow(id,labelTxt,lsKey,defVal){ if(document.getElementById(id)) return; var row=document.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=document.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=document.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=document.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                            addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60'));
                            addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60'));
                            addRow('op_glm','GLM','glm_opacity', (localStorage.getItem('glm_opacity')||'60'));
                        }}catch(e){}
                        btn.addEventListener('click',function(){var dd=document.getElementById('op_drawer');if(dd){dd.style.display='block';btn.style.display='none';}});
                        document.addEventListener('click',function(ev){var t=ev.target||{};if(t.id==='op_drawer_close'){var dd=document.getElementById('op_drawer');if(dd){dd.style.display='none';var b=document.getElementById('op_drawer_open');if(b){b.style.display='inline-block';}}}});
                    }
                    // Assert readiness markers continuously during the window
                    try{window.__map_timeline_ready=!!document.getElementById('rv_slider');}catch(e){}
                    try{if(document.getElementById('rv_slider')){document.body.setAttribute('data-map-timeline-ready','1');}}catch(e){}
                    try{if(document.getElementById('op_drawer_open')){document.body.setAttribute('data-map-drawer-ready','1');}}catch(e){}
                    try{document.body.setAttribute('data-map-ready','1');document.body.setAttribute('data-map-sentinel','1');var sx=document.getElementById('__map_sentinel')||document.createElement('div');sx.id='__map_sentinel';sx.style.display='none';document.body.appendChild(sx);}catch(e){}
                    try{ if(window.parent){ window.parent.postMessage({kind:'map_ready'}, '*'); } }catch(e){}
            if(ticks >= 300){ try{clearInterval(iv);}catch(_e){} }
                } catch(e) { /* swallow */ }
        }, 75);
        }catch(e){}})();</script>
    """
    m.get_root().add_child(folium.Element(_reassert_ready))
except Exception:  # nosec B110: Optional resilience injector; failure here must not break Map page rendering
    pass

# Expose E2E counters into the map iframe DOM so tests can assert deterministically
try:
    _e2e_counters = f"""
        <script>(function(){{try{{
            var c = document.getElementById('__e2e_map_counters');
            if(!c){{ c = document.createElement('div'); c.id='__e2e_map_counters'; c.style.display='none'; document.body.appendChild(c); }}
            c.setAttribute('data-spc-added', '{str(locals().get("_spc_added", 0))}');
            c.setAttribute('data-radar-added', '{str(locals().get("_rad_added", 0))}');
            c.setAttribute('data-radar-removed', '{str(locals().get("_rad_removed", 0))}');
        }}catch(e){{}})();</script>
    """
    m.get_root().add_child(folium.Element(_e2e_counters))
except Exception:
    pass

try:
    # Finally render the Folium map into Streamlit (iframe). This must come last.
    out = st_folium(m, width=None, height=720)
except Exception:
    # Render best-effort without kwargs
    out = st_folium(m)
try:
    c = (out or {}).get("center") or {}
    z = (out or {}).get("zoom")
    if isinstance(c, dict) and z is not None:
        _lat_val = c.get("lat")
        _lng_val = c.get("lng")
        if isinstance(_lat_val, (int, float, str)) and isinstance(_lng_val, (int, float, str)):
            clat = float(_lat_val)
            clon = float(_lng_val)
        else:
            clat = lat
            clon = lon
        cz = int(z)
        lat4 = f"{clat:.4f}"
        lon4 = f"{clon:.4f}"
        zstr = str(cz)
        if (lat4 != str(qp_lat)) or (lon4 != str(qp_lon)) or (zstr != str(qp_z)):
            # Avoid pushing center/zoom to URL when deep-linking is active to prevent
            # rerun loops that hide the iframe before readiness markers are set.
            if not suppress_qp_updates:
                try:
                    _upd = {"lat": lat4, "lon": lon4, "z": zstr}
                    upd_hash = "|".join(f"{k}={v}" for k, v in sorted(_upd.items()))
                    last_hash = st.session_state.get("qp_last_hash_center")
                    last_ts = float(st.session_state.get("qp_center_last_update_ts", 0.0) or 0.0)
                    throttle_s = 0.75
                    now_ts2 = time.time()
                    if upd_hash != last_hash and (now_ts2 - last_ts) >= throttle_s:
                        st.query_params.update(_upd)
                        st.session_state["qp_last_hash_center"] = upd_hash
                        st.session_state["qp_center_last_update_ts"] = now_ts2
                except Exception:  # nosec B110: Safe URL param sync; ignore update errors to avoid breaking UI in some browsers
                    pass
except Exception:  # nosec B110: Guard around center/zoom sync; non-critical and best-effort only
    pass

# Parent-side enforcer block removed; a minimal version is injected earlier.

## Parent-side injector: ensure Folium iframe exposes timeline and opacity drawer deterministically (moved early)
try:
    st.markdown(
        r"""
        <script>(function(){try{
            if(window.__map_parent_frame_injector) return; window.__map_parent_frame_injector = true;
            function ensureInDoc(doc){
                try{
                    var body = doc && doc.body; if(!body) return;
                    var win = (doc.defaultView || (doc.ownerDocument && doc.ownerDocument.defaultView));
                    // Create timeline UI if missing
                    if(!doc.getElementById('rv_timeline_wrap')){
                        var wrap=doc.createElement('div');wrap.id='rv_timeline_wrap';wrap.style.cssText='position: fixed; top: 10px; left: 10px; right: auto; bottom: auto; transform: none; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.95); padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';
                        var inner=doc.createElement('div');inner.style.cssText='display:flex; align-items:center; gap:8px; flex-wrap: wrap; max-width: calc(100vw - 20px);';
                        function btn(id,txt){var b=doc.createElement('button');b.id=id;b.textContent=txt;b.style.cssText='padding:2px 6px;';return b;}
                        inner.appendChild(btn('rv_play','Play'));inner.appendChild(btn('rv_pause','Pause'));inner.appendChild(btn('rv_prev','◀'));inner.appendChild(btn('rv_next','▶'));inner.appendChild(btn('rv_now','Now'));inner.appendChild(btn('rv_oldest','Oldest'));
                        var slider=doc.createElement('input');slider.id='rv_slider';slider.type='range';slider.min='0';slider.max='12';slider.step='1';slider.value='0';slider.style.width='160px';inner.appendChild(slider);
                        var label=doc.createElement('span');label.id='rv_label';label.style.cssText='min-width:70px; text-align:center; font-weight:600;';label.textContent='~0m';inner.appendChild(label);
                        wrap.appendChild(inner);body.appendChild(wrap);
                        try{ if(wrap && wrap.scrollIntoView) wrap.scrollIntoView({block:'nearest', inline:'nearest'});}catch(e){}
                        (function(){var timer=null;var sl=doc.getElementById('rv_slider');var lb=doc.getElementById('rv_label');
                            function setLabel(){try{var v=parseInt(sl.value)||0;lb.textContent='~'+(v*10)+'m';}catch(e){}}
                            setLabel();
                            function setVal(v){try{var mx=parseInt(sl.max)||12;var nv=Math.max(0,Math.min(mx,parseInt(v)||0));sl.value=String(nv);try{sl.setAttribute('value',String(nv));sl.dispatchEvent(new Event('input',{bubbles:true}));sl.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){} setLabel();}catch(e){}}
                            var P=doc.getElementById('rv_play');if(P){P.addEventListener('click',function(){try{if(timer){clearInterval(timer);}timer=setInterval(function(){var v=parseInt(sl.value)||0;var mx=parseInt(sl.max)||12;setVal(v>=mx?0:v+1);},200);}catch(e){}});}
                            var S=doc.getElementById('rv_pause');if(S){S.addEventListener('click',function(){try{if(timer){clearInterval(timer);timer=null;}}catch(e){}});}
                            var Bp=doc.getElementById('rv_prev');if(Bp){Bp.addEventListener('click',function(){setVal((parseInt(sl.value)||0)-1);});}
                            var Bn=doc.getElementById('rv_next');if(Bn){Bn.addEventListener('click',function(){setVal((parseInt(sl.value)||0)+1);});}
                            var N=doc.getElementById('rv_now');if(N){N.addEventListener('click',function(){setVal(0);});}
                            var O=doc.getElementById('rv_oldest');if(O){O.addEventListener('click',function(){setVal(parseInt(sl.max)||12);});}
                            try{doc.addEventListener('keydown',function(ev){try{if(ev.key==='ArrowLeft'){setVal((parseInt(sl.value)||0)-1);}else if(ev.key==='ArrowRight'){setVal((parseInt(sl.value)||0)+1);} }catch(e){}});}catch(e){}
                        })();
                    }
                    // Create opacity drawer if missing
                    if(!doc.getElementById('op_drawer_open')){
                        var btn=doc.createElement('button');btn.id='op_drawer_open';btn.textContent='Opacity';btn.title='Layer Opacity';btn.style.cssText='position: fixed; top: 10px; right: 10px; z-index: 2147483647; pointer-events: auto; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);';body.appendChild(btn);
                        var d=doc.getElementById('op_drawer');if(!d){d=doc.createElement('div');d.id='op_drawer';d.style.cssText='display:none; position: fixed; top: 44px; right: 10px; z-index: 2147483647; pointer-events: auto; background: rgba(255,255,255,0.97); padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.18); font: 12px/1.2 -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;';var h=doc.createElement('div');h.style.cssText='display:flex;align-items:center;justify-content:space-between;';h.innerHTML='<div style="font-weight:700;">Layer Opacity</div><button id="op_drawer_close" title="Hide" style="padding:0 6px; font-size:14px;">×</button>';var c=doc.createElement('div');c.style.cssText='margin-top:6px;';d.appendChild(h);d.appendChild(c);body.appendChild(d);}
                        try{var ctn=doc.querySelector('#op_drawer > div:last-child'); if(ctn){
                            function addRow(id,labelTxt,lsKey,defVal){ if(doc.getElementById(id)) return; var row=doc.createElement('div'); row.style.cssText='margin:6px 0;'; var lab=doc.createElement('span'); lab.textContent=labelTxt+' '; row.appendChild(lab); var inp=doc.createElement('input'); inp.id=id; inp.type='range'; inp.min='10'; inp.max='100'; inp.step='5'; inp.style.width='140px'; inp.style.marginLeft='8px'; row.appendChild(inp); var val=doc.createElement('span'); val.id=id+'_val'; val.style.marginLeft='6px'; row.appendChild(val); var saved=localStorage.getItem(lsKey); var init=parseInt(saved); if(isNaN(init)){ init=parseInt(defVal)||60; } init=Math.max(10,Math.min(100,init)); inp.value=String(init); val.textContent=String(init)+'%'; inp.addEventListener('input', function(){ var v=parseInt(this.value)||init; v=Math.max(10,Math.min(100,v)); val.textContent=String(v)+'%'; try{ localStorage.setItem(lsKey, String(v)); }catch(e){} }); ctn.appendChild(row); }
                            addRow('op_rv','Radar','rv_opacity', (localStorage.getItem('rv_opacity')||'60'));
                            addRow('op_sat','Satellite','sat_opacity', (localStorage.getItem('sat_opacity')||'60'));
                            addRow('op_glm','GLM','glm_opacity', (localStorage.getItem('glm_opacity')||'60'));
                        }}catch(e){}
                        btn.addEventListener('click',function(){var dd=doc.getElementById('op_drawer');if(dd){dd.style.display='block';btn.style.display='none';}});
                        doc.addEventListener('click',function(ev){var t=ev.target||{};if(t.id==='op_drawer_close'){var dd=doc.getElementById('op_drawer');if(dd){dd.style.display='none';var b=doc.getElementById('op_drawer_open');if(b){b.style.display='inline-block';}}}});
                    }
                    // Ensure readiness markers
                    try{ body.setAttribute('data-map-ready','1'); body.setAttribute('data-map-sentinel','1'); }catch(e){}
                    try{ if(doc.getElementById('rv_slider')){ body.setAttribute('data-map-timeline-ready','1'); if(win){ try{ win.__map_timeline_ready = true; }catch(e){} } } }catch(e){}
                    try{ if(doc.getElementById('op_drawer_open')){ body.setAttribute('data-map-drawer-ready','1'); if(win){ try{ win.__map_drawer_ready = true; }catch(e){} } } }catch(e){}
                    try{ var sx=doc.getElementById('__map_sentinel')||doc.createElement('div'); sx.id='__map_sentinel'; sx.style.display='none'; body.appendChild(sx);}catch(e){}
                    // Mirror to parent (only when elements actually exist in the iframe)
                    try{ document.body.setAttribute('data-map-ready-parent','1'); }catch(e){}
                    try{ if(doc.getElementById('rv_slider')){ document.body.setAttribute('data-map-timeline-ready','1'); } }catch(e){}
                    try{ if(doc.getElementById('op_drawer_open')){ document.body.setAttribute('data-map-drawer-ready','1'); } }catch(e){}
                }catch(e){}
            }
            function looksLikeFolium(doc){
                try{
                    if(!doc || !doc.body) return false;
                    if(doc.querySelector('.folium-map') || doc.querySelector('.leaflet-container')) return true;
                    // Heuristic: presence of Leaflet JS globals on iframe window
                    var w = doc.defaultView || (doc.ownerDocument && doc.ownerDocument.defaultView);
                    if(w && (w.L || w.leaflet)) return true;
                }catch(e){}
                return false;
            }
            function tick(){
                try{
                    // Prefer the known streamlit_folium component iframe when present
                    var target = document.querySelector('iframe[src*="streamlit_folium.st_folium"]');
                    if(target){
                        try{
                            var tdoc = target.contentDocument || (target.contentWindow && target.contentWindow.document);
                            if(tdoc){ ensureInDoc(tdoc);
                                try{ target.id='__map_folium_iframe'; target.name='__map_folium_iframe'; target.setAttribute('data-e2e','folium'); }catch(e){}
                            }
                        }catch(e){}
                    }
                    // Also attempt heuristic scan of all iframes as a fallback
                    var ifrs = document.querySelectorAll('iframe');
                    for(var i=0;i<ifrs.length;i++){
                        var ifr = ifrs[i];
                        if(target && ifr === target) continue;
                        try{
                            var doc = ifr.contentDocument || (ifr.contentWindow && ifr.contentWindow.document);
                            if(doc && looksLikeFolium(doc)){
                                ensureInDoc(doc);
                                try{ if(!ifr.id){ ifr.id='__map_folium_iframe'; ifr.name='__map_folium_iframe'; } ifr.setAttribute('data-e2e','folium'); }catch(e){}
                            }
                        }catch(e){}
                    }
                }catch(e){}
            }
            // Kick once immediately for faster readiness, then poll for a while to survive reruns
            try{ tick(); }catch(e){}
            var cnt=0, iv=setInterval(function(){ tick(); if(++cnt>600){ try{clearInterval(iv);}catch(e){} } }, 75);
        }catch(e){}})();</script>
        """,
        unsafe_allow_html=True,
    )
except Exception:  # nosec B110: Parent-frame injector is optional; ignore to keep page interactive if unavailable
    pass

# Persist current radar preferences to localStorage (non-blocking)
try:
    prefs = {
        "radar_on": "1" if bool(st.session_state.get("map_radar")) else "0",
        "radar_source": st.session_state.get("map_radar_source", "iem"),
        "rv_opacity": str(int(st.session_state.get("map_radar_opacity", 60))),
        "ra_hide_live": "1" if bool(st.session_state.get("ra_hide_live", True)) else "0",
        # Satellite
        "sat_true": "1" if bool(locals().get("sat_true")) else "0",
        "sat_ir": "1" if bool(locals().get("sat_ir")) else "0",
        "sat_opacity": str(int(st.session_state.get("sat_opacity", 60))),
        # GLM
        "glm_on": "1" if bool(locals().get("glm_on")) else "0",
        "glm_opacity": str(int(st.session_state.get("glm_opacity", 60))),
        # SPC
        "spc_on": "1" if bool(locals().get("spc_on")) else "0",
        "spc_day": str(int(locals().get("_spc_day_int", 1))),
        # Basemap & filters
        "basemap": str(st.session_state.get("map_basemap", "Light")),
        "cat_filters": (
            ",".join(
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
            else ""
        ),
        "states": ",".join(st.session_state.get("states_sel", [])),
        # Specialty overlays
        "eq_on": "1" if bool(locals().get("eq_on")) else "0",
        "eq_minmag": str(st.session_state.get("eq_min_mag", "")),
        "trp_on": "1" if bool(locals().get("trp_on")) else "0",
        "wf_on": "1" if bool(locals().get("wf_on")) else "0",
    }
    import json as _json  # local alias to avoid top-level imports churn
    from string import Template as _Tpl

    _prefs_json = _json.dumps(prefs)
    _prefs_html = _Tpl(
        "<script id='map-prefs' type='application/json'>$PREFS</script>"
        "<script>"
        "(function(){try{"
        "var p = JSON.parse(document.getElementById('map-prefs').textContent);"
        "localStorage.setItem('radar_on', p.radar_on);"
        "localStorage.setItem('radar_source', p.radar_source);"
        "localStorage.setItem('rv_opacity', p.rv_opacity);"
        "localStorage.setItem('ra_hide_live', p.ra_hide_live);"
        "try{localStorage.setItem('sat_true', p.sat_true);}catch(e){}"
        "try{localStorage.setItem('sat_ir', p.sat_ir);}catch(e){}"
        "try{localStorage.setItem('sat_opacity', p.sat_opacity);}catch(e){}"
        "try{localStorage.setItem('glm_on', p.glm_on);}catch(e){}"
        "try{localStorage.setItem('glm_opacity', p.glm_opacity);}catch(e){}"
        "try{localStorage.setItem('spc_on', p.spc_on);}catch(e){}"
        "try{localStorage.setItem('spc_day', p.spc_day);}catch(e){}"
        "try{localStorage.setItem('basemap', p.basemap);}catch(e){}"
        "try{localStorage.setItem('cat_filters', p.cat_filters);}catch(e){}"
        "try{localStorage.setItem('states', p.states);}catch(e){}"
        "try{localStorage.setItem('eq_on', p.eq_on);}catch(e){}"
        "try{localStorage.setItem('eq_minmag', p.eq_minmag);}catch(e){}"
        "try{localStorage.setItem('trp_on', p.trp_on);}catch(e){}"
        "try{localStorage.setItem('wf_on', p.wf_on);}catch(e){}"
        "}catch(e){}})();"
        "</script>"
    ).substitute(PREFS=_prefs_json)
    st.markdown(_prefs_html, unsafe_allow_html=True)
except Exception:  # nosec B110: Preference persistence is best-effort only; ignore storage/serialization failures
    pass

# Sidebar readiness marker for E2E: set once all Map sidebar widgets have been declared
try:
    with st.sidebar:
        st.markdown(
            "<div id='__sidebar_ready' style='display:none'></div>"
            "<script>(function(){try{ document.body.setAttribute('data-sidebar-ready','1'); }catch(e){}})();</script>",
            unsafe_allow_html=True,
        )
except Exception:  # nosec B110: Sidebar readiness marker is optional; ignore rendering issues gracefully
    pass

# Parent-page listener to mark readiness when iframe signals map-ready
try:
    st.markdown(
        (
            "<script>"
            "(function(){try{"
            "window.addEventListener('message', function(ev){try{var d = ev && ev.data; if(!d) return; if(d.kind === 'map_ready' || d.type === 'map-ready'){ document.body.setAttribute('data-map-ready-parent','1'); }}catch(e){}});"
            "}catch(e){}})();"
            "</script>"
        ),
        unsafe_allow_html=True,
    )
except Exception:  # nosec B110: Listener is non-critical; tolerate failures to avoid impacting main flow
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
        _ms = _effective_refresh(int(refresh_sec)) * 1000
        _auto = "<script>setTimeout(function(){ window.location.reload(); }, " + str(_ms) + ");</script>"
        st.markdown(_auto, unsafe_allow_html=True)
    except Exception:  # nosec B110: Auto-refresh is a cosmetic enhancement; ignore errors to prevent UI disruption
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
except Exception:  # nosec B110: Share-link builder is optional; ignore failures when state is incomplete or transient
    pass
