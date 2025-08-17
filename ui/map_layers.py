from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Callable

import folium
import streamlit as st
from folium import plugins

from .http_client import fetch_json

# ---------- SPC Outlooks ----------
SPC_COLORS = {
    "TSTM": "#a1d99b",
    "MRGL": "#74c476",
    "SLGT": "#31a354",
    "ENH": "#ffcc00",
    "MDT": "#ff7f00",
    "HIGH": "#e31a1c",
}


def fetch_spc_outlook(day: int = 1) -> list[dict]:
    try:
        d = max(1, min(3, int(day)))
        url = "https://mesonet.agron.iastate.edu/geojson/spc_outlooks.geojson"
        data = fetch_json(url, params={"day": d}, ttl=600)
        if isinstance(data, dict):
            return list(data.get("features") or [])
    except Exception:
        return []
    return []


def add_spc_outlooks(m: folium.Map, on: bool, day: int) -> None:
    if not on:
        return
    feats = fetch_spc_outlook(day)
    if not feats:
        return
    grp = folium.FeatureGroup(name=f"SPC Outlook (Day {day})", show=True)
    for f in feats:
        try:
            geom = f.get("geometry") or {}
            props = f.get("properties") or {}
            cat = (props.get("category") or props.get("label") or "TSTM").upper()
            color = SPC_COLORS.get(cat, "#6baed6")
            fast = bool(st.session_state.get("map_fast_mode", True))
            if geom.get("type") == "Polygon":
                coords = geom.get("coordinates") or []
                if coords:
                    latlon = [(lat, lon) for lon, lat in coords[0]]
                    folium.Polygon(
                        latlon,
                        color=color,
                        weight=1 if fast else 2,
                        fill=not fast,
                        fill_color=color,
                        fill_opacity=0.15 if fast else 0.25,
                        popup=f"SPC Day {day} {cat}",
                    ).add_to(grp)
            elif geom.get("type") == "MultiPolygon":
                for poly in geom.get("coordinates") or []:
                    if poly:
                        latlon = [(lat, lon) for lon, lat in poly[0]]
                        folium.Polygon(
                            latlon,
                            color=color,
                            weight=1 if fast else 2,
                            fill=not fast,
                            fill_color=color,
                            fill_opacity=0.15 if fast else 0.25,
                            popup=f"SPC Day {day} {cat}",
                        ).add_to(grp)
        except Exception:
            continue
    grp.add_to(m)


# ---------- Earthquakes (USGS) ----------
def fetch_usgs_quakes() -> list[dict]:
    try:
        url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
        data = fetch_json(url, ttl=600)
        if isinstance(data, dict):
            return list(data.get("features") or [])
    except Exception:
        return []
    return []


def add_earthquakes(m: folium.Map, on: bool, min_mag: float) -> None:
    if not on:
        return
    try:
        eq = fetch_usgs_quakes()
    except Exception:
        eq = []
    if not eq:
        return
    layer = folium.FeatureGroup(name="Earthquakes (USGS)", show=True)
    for f in eq:
        try:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            try:
                mv = props.get("mag")
                mag = float(str(mv)) if mv is not None else 0.0
            except Exception:
                mag = 0.0
            if mag < float(min_mag):
                continue
            coords = geom.get("coordinates") or [None, None]
            lon, lat = (coords[0], coords[1]) if isinstance(coords, list) else (None, None)
            if lat is None or lon is None:
                continue
            radius = max(3, min(14, 3 + mag * 2))
            popup = f"M{mag:.1f} — {props.get('place', '')}"
            folium.CircleMarker(
                (lat, lon),
                radius=radius,
                color="#ff7f00",
                fill=True,
                fill_opacity=0.9,
                popup=popup,
            ).add_to(layer)
        except Exception:
            continue
    layer.add_to(m)


# ---------- Tropical (NHC) ----------
def fetch_nhc_current() -> list[dict]:
    try:
        url = "https://www.nhc.noaa.gov/CurrentStorms.json"
        data = fetch_json(url, ttl=600)
        if isinstance(data, dict):
            feats: list[dict] = []
            for s in data.get("storms", []) or []:
                try:
                    lat = float(s.get("lat")) if s.get("lat") is not None else None
                    lon = float(s.get("lon")) if s.get("lon") is not None else None
                    if lat is None or lon is None:
                        continue
                    feats.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [lon, lat]},
                            "properties": s,
                        }
                    )
                except Exception:
                    continue
            return feats
    except Exception:
        return []
    return []


def add_tropical(m: folium.Map, on: bool) -> None:
    if not on:
        return
    try:
        storms = fetch_nhc_current()
    except Exception:
        storms = []
    if not storms:
        return
    layer = folium.FeatureGroup(name="Tropical Systems (NHC)", show=True)
    for f in storms:
        try:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None]
            lon, lat = (coords[0], coords[1]) if isinstance(coords, list) else (None, None)
            if lat is None or lon is None:
                continue
            name = props.get("name") or props.get("stormName") or "Tropical"
            status = props.get("status") or props.get("type") or ""
            popup = f"{name} — {status}"
            folium.Marker((lat, lon), icon=folium.Icon(color="red", icon="flag"), popup=popup).add_to(layer)
        except Exception:
            continue
    layer.add_to(m)


# ---------- Wildfires ----------
def fetch_wildfire_perimeters() -> list[dict]:
    try:
        url = (
            "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/"
            "Wildland_Fire_Perimeters/FeatureServer/0/query"
        )
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
        }
        data = fetch_json(url, params=params, ttl=600, timeout=25.0)
        if isinstance(data, dict):
            return list(data.get("features") or [])
    except Exception:
        return []
    return []


def add_wildfires(m: folium.Map, on: bool) -> None:
    if not on:
        return
    try:
        fires = fetch_wildfire_perimeters()
    except Exception:
        fires = []
    if not fires:
        return
    layer = folium.FeatureGroup(name="Wildfires", show=True)
    for f in fires:
        try:
            geom = f.get("geometry") or {}
            props = f.get("properties") or {}
            name = props.get("IncidentName") or props.get("Name") or "Wildfire"
            if geom.get("type") == "Polygon":
                coords = geom.get("coordinates") or []
                if coords:
                    latlon = [(lat, lon) for lon, lat in coords[0]]
                    folium.Polygon(
                        latlon,
                        color="#e31a1c",
                        weight=2,
                        fill=True,
                        fill_color="#fb6a4a",
                        fill_opacity=0.35,
                        popup=name,
                    ).add_to(layer)
            elif geom.get("type") == "MultiPolygon":
                for poly in geom.get("coordinates") or []:
                    if poly:
                        latlon = [(lat, lon) for lon, lat in poly[0]]
                        folium.Polygon(
                            latlon,
                            color="#e31a1c",
                            weight=2,
                            fill=True,
                            fill_color="#fb6a4a",
                            fill_opacity=0.35,
                            popup=name,
                        ).add_to(layer)
        except Exception:
            continue
    layer.add_to(m)


# ---------- Historical Timeline ----------
def fetch_history(area: str, start_ts: str, end_ts: str) -> list[dict]:
    try:
        url = "https://api.weather.gov/alerts"
        params = {"area": area, "start": start_ts, "end": end_ts}
        data = fetch_json(url, params=params, ttl=120)
        if isinstance(data, dict):
            return list(data.get("features") or [])
    except Exception:
        return []
    return []


def add_historical_timeline(
    m: folium.Map,
    states: list[str],
    hours_back: int,
    only_selected: bool,
    selected_events: Iterable[str] | None,
    only_triggers: bool,
    alert_matches_filters: Callable[[dict], bool],
    extract_county_fips: Callable[[dict], list[str]],
    target_fips: Iterable[str],
) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=int(hours_back))
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    hist_features: list[dict] = []
    for s in states:
        hist_features.extend(fetch_history(s, start_iso, end_iso))

    evset = set(selected_events) if (only_selected and selected_events) else None
    feats_for_timeline: list[dict] = []
    for f in hist_features:
        props = f.get("properties") or {}
        event = props.get("event")
        if evset and event not in evset:
            continue
        if only_triggers and not (
            alert_matches_filters(props) and (set(extract_county_fips(props)) & set(target_fips))
        ):
            continue
        geom = f.get("geometry") or {}
        if not geom:
            continue
        t = props.get("effective") or props.get("onset") or props.get("sent") or props.get("published")
        if not t:
            continue
        t = str(t).replace("Z", "Z")
        feats_for_timeline.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "time": t,
                    "style": {"color": "#6a51a3", "weight": 2, "fillColor": "#9e9ac8", "fillOpacity": 0.2},
                    "popup": props.get("headline") or props.get("event") or "alert",
                },
            }
        )

    if feats_for_timeline:
        try:
            plugins.TimestampedGeoJson(
                {"type": "FeatureCollection", "features": feats_for_timeline},
                period="PT1H",
                duration="PT30M",
                transition_time=200,
                auto_play=False,
                loop=False,
                add_last_point=False,
            ).add_to(m)
        except Exception:
            pass


# ---------- LSR (Storm Reports) ----------
def fetch_lsr(states: list[str], hours: int = 24) -> list[dict]:
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=max(1, min(72, int(hours))))
        sts = start.strftime("%Y-%m-%dT%H:%MZ")
        ets = end.strftime("%Y-%m-%dT%H:%MZ")
        states_param = ",".join([s.strip() for s in states if s])
        url = "https://mesonet.agron.iastate.edu/geojson/lsr.php"
        params = {"sts": sts, "ets": ets}
        if states_param:
            params["state"] = states_param
        data = fetch_json(url, params=params, ttl=120, timeout=20.0)
        if isinstance(data, dict):
            return list(data.get("features") or [])
    except Exception:
        return []
    return []


def add_lsr_layers(
    m: folium.Map,
    states: list[str],
    hours: int,
    show: bool,
    show_hail: bool,
    show_wind: bool,
    show_tor: bool,
    show_paths: bool,
) -> None:
    if not show:
        return
    try:
        lsr_feats = fetch_lsr(states, hours)
    except Exception:
        lsr_feats = []
    l_hail = folium.FeatureGroup(name="LSR - Hail", show=True)
    l_wind = folium.FeatureGroup(name="LSR - Wind", show=True)
    l_tor = folium.FeatureGroup(name="LSR - Tornado", show=True)
    # If fast mode enabled, cluster markers
    use_cluster = bool(st.session_state.get("map_fast_mode", True))
    hail_cluster = plugins.MarkerCluster(name="LSR Hail Cluster").add_to(l_hail) if use_cluster else None
    wind_cluster = plugins.MarkerCluster(name="LSR Wind Cluster").add_to(l_wind) if use_cluster else None
    tor_cluster = plugins.MarkerCluster(name="LSR Tornado Cluster").add_to(l_tor) if use_cluster else None
    l_tor_paths = folium.FeatureGroup(name="LSR - Tornado Paths", show=True)
    added_any = False
    tor_points: list[tuple[datetime, float, float]] = []

    def _parse_ts(v: str | None) -> datetime | None:
        if not v:
            return None
        try:
            s = str(v).replace(" ", "T").replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _haversine_km(lat1, lon1, lat2, lon2):
        from math import atan2, cos, radians, sin, sqrt

        earth_radius = 6371.0
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return earth_radius * c

    for f in lsr_feats:
        try:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None]
            lon, lat = (coords[0], coords[1]) if isinstance(coords, list) and len(coords) >= 2 else (None, None)
            if lat is None or lon is None:
                continue
            typ = (props.get("typetext") or props.get("type") or "").upper()
            mag = props.get("magnitude")
            city = props.get("city") or props.get("location") or ""
            county = props.get("county") or ""
            valid = props.get("valid") or props.get("time") or ""
            remark = props.get("remark") or props.get("remarks") or ""
            popup = (
                f"<b>{typ or 'REPORT'}</b> {('• ' + str(mag)) if mag not in (None, '') else ''}"
                f"<br/>{city}{(', ' + county) if county else ''}"
                f"<br/>{valid}"
                f"<br/>{remark[:240]}"
            )
            if "HAIL" in typ and show_hail:
                try:
                    r = 4 + float(mag or 1) * 3
                except Exception:
                    r = 6
                target = hail_cluster if hail_cluster is not None else l_hail
                folium.CircleMarker(
                    location=(lat, lon),
                    radius=r,
                    color="#2c7fb8",
                    fill=True,
                    fill_opacity=0.8,
                    popup=popup,
                ).add_to(target)
                added_any = True
            elif ("WND" in typ or "WIND" in typ) and show_wind:
                try:
                    r = 4 + float(mag or 40) / 10.0
                except Exception:
                    r = 6
                target = wind_cluster if wind_cluster is not None else l_wind
                folium.CircleMarker(
                    location=(lat, lon),
                    radius=r,
                    color="#238b45",
                    fill=True,
                    fill_opacity=0.9,
                    popup=popup,
                ).add_to(target)
                added_any = True
            elif ("TOR" in typ or "TORNADO" in typ) and show_tor:
                target = tor_cluster if tor_cluster is not None else l_tor
                folium.CircleMarker(
                    location=(lat, lon),
                    radius=6,
                    color="#d73027",
                    fill=True,
                    fill_opacity=0.9,
                    popup=popup,
                ).add_to(target)
                added_any = True
                if show_paths:
                    ts = _parse_ts(valid)
                    if ts is not None:
                        tor_points.append((ts, float(lat), float(lon)))
        except Exception:
            continue

    if added_any:
        if show_hail:
            l_hail.add_to(m)
        if show_wind:
            l_wind.add_to(m)
        if show_tor:
            l_tor.add_to(m)
        if show_paths and tor_points:
            try:
                tor_points.sort(key=lambda x: x[0])
                tracks: list[list[tuple[float, float]]] = []
                current: list[tuple[float, float]] = []
                last_ts = None
                last_lat = last_lon = None
                for ts, lat, lon in tor_points:
                    if last_ts is None:
                        current = [(lat, lon)]
                    else:
                        dt_min = (ts - last_ts).total_seconds() / 60.0
                        has_last = last_lat is not None and last_lon is not None
                        dist_km = _haversine_km(last_lat, last_lon, lat, lon) if has_last else 0.0
                        if dt_min > 45 or dist_km > 40:
                            if current:
                                tracks.append(current)
                            current = [(lat, lon)]
                        else:
                            current.append((lat, lon))
                    last_ts, last_lat, last_lon = ts, lat, lon
                if current:
                    tracks.append(current)
                for seg in tracks:
                    if len(seg) >= 2:
                        folium.PolyLine(
                            seg,
                            color="#d73027",
                            weight=3,
                            opacity=0.9,
                            dash_array="5,7",
                        ).add_to(l_tor_paths)
                if tracks:
                    l_tor_paths.add_to(m)
            except Exception:
                pass
