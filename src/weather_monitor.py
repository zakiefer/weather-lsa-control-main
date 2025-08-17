import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt

import requests

from .config import settings as cfg
from .config.settings import (
    ALLOWED_CERTAINTY,
    ALLOWED_SEVERITIES,
    ALLOWED_URGENCY,
    CENTER_LAT,
    CENTER_LON,
    FORCE_ALERT,
    FORCE_EVENT,
    MAX_DISTANCE_MI,
    NWS_TIMEOUT_SECONDS,
    RULES_FILE,
    STATE_CODES,
    STORM_HOLD_TIME_HOURS,
    TARGET_COUNTIES,
    TARGET_COUNTY_FIPS,
    TRIGGER_EVENTS,
)
from .db import (
    any_area_blocks_pause,
    enqueue_mutation,
    get_campaigns_for_areas,
    get_latest_alert_for_cap,
    is_breaker_open,
    is_duplicate_action,
    mark_breaker_notified,
    record_action_dedupe,
    should_notify_breaker,
    update_area_cooldown,
    upsert_alert,
    upsert_alert_cap,
)
from .lsa_client import LSAClient
from .metrics import inc_alerts_seen, inc_api_errors, inc_cap_dedupe_skipped, inc_notifications_sent, time_nws_fetch
from .notifier import Notifier
from .observability import capture_error
from .rules import evaluate as eval_rules
from .rules import load_rules
from .tracing import start_span
from .utils.time import within_quiet_hours


class WeatherMonitor:
    def __init__(self, credentials):
        self.lsa_client = LSAClient(credentials)
        self.storm_hold_time = timedelta(hours=STORM_HOLD_TIME_HOURS)
        self.notifier = Notifier()
        # Load rules if configured
        self._rules = []
        try:
            if RULES_FILE:
                self._rules = load_rules(RULES_FILE)
        except Exception as e:
            logging.warning(f"Failed to load rules from {RULES_FILE}: {e}")
        # Persist state so we honor hold time across runs
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        self.log_dir = os.path.join(project_root, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.state_file = os.path.join(self.log_dir, "storm_state.json")
        # Backward-compatible: migrate from old src/logs location if present
        old_state = os.path.join(current_dir, "logs", "storm_state.json")
        if os.path.exists(old_state) and not os.path.exists(self.state_file):
            try:
                os.makedirs(self.log_dir, exist_ok=True)
                with open(old_state) as fsrc, open(self.state_file, "w") as fdst:
                    fdst.write(fsrc.read())
                os.remove(old_state)
            except Exception:
                pass

    # State helpers
    def clear_hold(self):
        """Clear the last alert timestamp, effectively removing the hold window."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file) as f:
                    data = json.load(f)
            else:
                data = {}
            data.pop("last_alert_utc", None)
            data.pop("last_county_fips", None)
            with open(self.state_file, "w") as f:
                json.dump(data, f)
            logging.info("Cleared storm hold state.")
        except Exception as e:
            logging.warning(f"Failed to clear storm hold state: {e}")

    def _load_state(self):
        """Return persisted storm state dict from logs/storm_state.json (or {})."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_state(self, data: dict):
        """Persist storm state dict to logs/storm_state.json (best-effort)."""
        try:
            with open(self.state_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logging.warning(f"Failed to save storm state: {e}")

    def _hold_active(self) -> bool:
        """True if we're within the configured storm hold window since last alert."""
        state = self._load_state()
        last_alert_iso = state.get("last_alert_utc")
        if not last_alert_iso:
            return False
        try:
            last_alert_dt = datetime.fromisoformat(last_alert_iso)
            now_utc = datetime.now(timezone.utc)
            return (now_utc - last_alert_dt) < self.storm_hold_time
        except Exception:
            return False

    # Alert processing helpers
    def _force_alert(self) -> bool:
        """Simulate an alert for testing; updates state, records, and notifies."""
        logging.info(f"FORCE_ALERT active - simulating alert: {FORCE_EVENT}")
        state = self._load_state()
        state["last_alert_utc"] = datetime.now(timezone.utc).isoformat()
        state.pop("last_county_fips", None)
        self._save_state(state)
        try:
            ahash = hashlib.sha256(f"forced:{FORCE_EVENT}:{state['last_alert_utc']}".encode()).hexdigest()
            upsert_alert(ahash, "forced", state["last_alert_utc"], None, None)
        except Exception:
            pass
        try:
            self.notifier.notify(
                subject=f"Weather Alert (Forced): {FORCE_EVENT}",
                body=f"Forced trigger for testing at {datetime.now(timezone.utc).isoformat()}Z",
            )
        except Exception:
            pass
        return True

    def _fetch_alerts_for_state(self, state_code: str) -> list[dict]:
        """Fetch active NWS alerts for the given state code; returns feature dicts or []."""
        url = f"https://api.weather.gov/alerts/active?area={state_code}"
        with start_span("nws.fetch", {"component": "nws", "state": state_code}):
            with time_nws_fetch():
                resp = requests.get(
                    url,
                    headers={"User-Agent": "WeatherAlertBot/1.0"},
                    timeout=NWS_TIMEOUT_SECONDS,
                )
        if resp.status_code != 200:
            logging.warning(f"NWS request failed for {state_code}: HTTP {resp.status_code}")
            try:
                inc_api_errors()
            except Exception:
                pass
            try:
                capture_error(
                    RuntimeError("nws_fetch_failed"),
                    tags={"component": "nws", "state": state_code},
                    extras={"status": resp.status_code, "url": url},
                )
            except Exception:
                pass
            return []
        try:
            return resp.json().get("features", [])
        except Exception:
            return []

    def _alert_matches_filters(self, props: dict) -> bool:
        """Return True if alert properties match configured event/severity/urgency/certainty filters."""
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

    def _extract_county_fips(self, props: dict) -> set:
        """Extract county FIPS from alert geocode as a set of last-5-digit strings."""
        geocode = props.get("geocode", {}) or {}
        fips6 = geocode.get("FIPS6") or geocode.get("FIPS") or []
        county_fips: set[str] = set()
        for code in fips6:
            code = str(code)
            if len(code) >= 5:
                county_fips.add(code[-5:])
        return county_fips

    def _centroid_within_radius(self, alert: dict, clat: float, clon: float, max_distance_mi: float) -> bool:
        """Check if the first polygon centroid lies within the given radius of (clat, clon)."""
        try:
            geom = alert.get("geometry") or {}
            coords = []
            if geom.get("type") == "Polygon":
                coords = geom.get("coordinates", [])
            elif geom.get("type") == "MultiPolygon":
                polys = geom.get("coordinates", [])
                coords = polys[0] if polys else []
            if not (coords and coords[0]):
                return True
            ring = coords[0]
            lat_sum = lon_sum = 0.0
            n = len(ring)
            for lon, lat in ring:
                lat_sum += lat
                lon_sum += lon
            cent_lat = lat_sum / n
            cent_lon = lon_sum / n
            earth_radius_mi = 3958.8
            dlat = radians(cent_lat - clat)
            dlon = radians(cent_lon - clon)
            a = sin(dlat / 2) ** 2 + cos(radians(clat)) * cos(radians(cent_lat)) * sin(dlon / 2) ** 2
            c = 2 * atan2(sqrt(a), sqrt(1 - a))
            dist = earth_radius_mi * c
            return dist <= max_distance_mi
        except Exception:
            return True

    def _poly_hash(self, alert: dict) -> str | None:
        """Stable SHA-256 hash of alert geometry JSON for de-duplication, or None."""
        try:
            geom = alert.get("geometry") or {}
            if geom.get("type") in ("Polygon", "MultiPolygon"):
                import hashlib as _hl

                return _hl.sha256(json.dumps(geom, sort_keys=True).encode("utf-8")).hexdigest()
        except Exception:
            pass
        return None

    def _cap_is_stale(self, cap_id: str | None, effective_at: str | None) -> bool:
        """True if we already recorded a same/newer effective time for this CAP ID."""
        if not (cap_id and effective_at):
            return False
        try:
            _prev_id, prev_eff = get_latest_alert_for_cap(cap_id)
            return bool(prev_eff and str(prev_eff) >= str(effective_at))
        except Exception:
            return False

    def _record_alert_and_notify(
        self,
        source: str,
        event: str | None,
        effective_at: str | None,
        cap_id: str | None,
        county_fips: list[str] | None,
        area_desc: str | None,
        poly_hash: str | None,
        severity: str | None,
    ) -> None:
        """Record the alert in DB (CAP-aware) and send a notification; update state."""
        try:
            inc_alerts_seen()
        except Exception:
            pass
        state = self._load_state()
        state["last_alert_utc"] = effective_at or datetime.now(timezone.utc).isoformat()
        if county_fips is not None:
            try:
                state["last_county_fips"] = sorted(list(county_fips))
            except Exception:
                state["last_county_fips"] = []
        else:
            state.pop("last_county_fips", None)
        self._save_state(state)
        try:
            if effective_at:
                if cap_id:
                    # Preferred: store by CAP ID for dedupe
                    upsert_alert_cap(
                        cap_id,
                        effective_at,
                        poly_hash,
                        source,
                        (",".join(sorted(county_fips)) if county_fips is not None else (area_desc or "")),
                        severity,
                    )
                else:
                    # No CAP ID: fall back to deterministic hash similar to legacy paths
                    if county_fips is not None:
                        ahash = hashlib.sha256(
                            (f"{source}:{event}:{state['last_alert_utc']}:{sorted(county_fips)}").encode()
                        ).hexdigest()
                        upsert_alert(
                            ahash,
                            source,
                            state["last_alert_utc"],
                            ",".join(sorted(county_fips)),
                            severity,
                        )
                    else:
                        ahash = hashlib.sha256(
                            f"{source}:{event}:{state['last_alert_utc']}:name:{area_desc}".encode()
                        ).hexdigest()
                        upsert_alert(ahash, source, state["last_alert_utc"], area_desc, severity)
        except Exception:
            pass
        try:
            subj = f"Weather Alert: {event or 'unknown'}"
            body = (
                f"Trigger event {event} in counties {county_fips}"
                if county_fips is not None
                else f"Trigger event {event} via name fallback in area {area_desc}"
            )
            self.notifier.notify(subject=subj, body=body)
            inc_notifications_sent()
        except Exception:
            pass

    def check_severe_weather(self):
        """Check for active severe weather alerts in target areas"""
        try:
            if FORCE_ALERT:
                return self._force_alert()
            for st in STATE_CODES:
                for alert in self._fetch_alerts_for_state(st):
                    props = alert.get("properties", {})
                    if not self._alert_matches_filters(props):
                        continue
                    event = props.get("event")
                    cap_id = props.get("id") or props.get("capId") or props.get("cap_id")
                    effective_at = (
                        props.get("effective") or props.get("onset") or props.get("sent") or props.get("published")
                    )
                    area_desc = props.get("areaDesc", "")
                    severity = props.get("severity")

                    county_fips_in_alert = self._extract_county_fips(props)
                    within = bool(county_fips_in_alert & TARGET_COUNTY_FIPS)
                    if within and CENTER_LAT and CENTER_LON and MAX_DISTANCE_MI > 0:
                        try:
                            clat = float(CENTER_LAT)
                            clon = float(CENTER_LON)
                            within = self._centroid_within_radius(alert, clat, clon, MAX_DISTANCE_MI)
                        except Exception:
                            pass

                    poly_hash = self._poly_hash(alert)
                    if self._cap_is_stale(cap_id, effective_at):
                        try:
                            inc_cap_dedupe_skipped()
                        except Exception:
                            pass
                        continue

                    if within:
                        logging.info(
                            "Severe weather alert active via FIPS: %s (%s)",
                            event,
                            county_fips_in_alert & TARGET_COUNTY_FIPS,
                        )
                        self._record_alert_and_notify(
                            source="nws",
                            event=event,
                            effective_at=effective_at,
                            cap_id=cap_id,
                            county_fips=sorted(list(county_fips_in_alert & TARGET_COUNTY_FIPS)),
                            area_desc=None,
                            poly_hash=poly_hash,
                            severity=severity,
                        )
                        return True

                    if any(county in area_desc for county in TARGET_COUNTIES):
                        logging.info(f"Severe weather alert active via name: {event}")
                        self._record_alert_and_notify(
                            source="nws",
                            event=event,
                            effective_at=effective_at,
                            cap_id=cap_id,
                            county_fips=None,
                            area_desc=area_desc,
                            poly_hash=poly_hash,
                            severity=severity,
                        )
                        return True
            # No active alerts found; check hold window
            if self._hold_active():
                state = self._load_state()
                last_alert_iso = state.get("last_alert_utc")
                try:
                    last_alert_dt = datetime.fromisoformat(last_alert_iso) if last_alert_iso else None
                    now_utc = datetime.now(timezone.utc)
                    remaining = self.storm_hold_time - (now_utc - last_alert_dt) if last_alert_dt else None
                    if remaining:
                        logging.info(f"Within storm hold window, keeping enabled ({remaining} remaining)")
                except Exception:
                    pass
                return True
            return False
        except Exception as e:
            logging.error(f"Weather API check failed: {e}")
            try:
                capture_error(e, tags={"component": "nws", "op": "check"})
            except Exception:
                pass
            return False

    def update_campaign_status(self):
        """Update campaign status based on weather conditions"""
        # Track alert hash/ID when enabling, so we can dedupe mutate calls
        # Kill switch: skip all actions
        try:
            if getattr(cfg, "KILL_SWITCH", False):
                logging.warning("KILL_SWITCH is active; skipping any enable/pause actions.")
                return
        except Exception:
            pass

        # Quiet hours: suppress mutates during a configured time window (local time)
        try:
            qh = getattr(cfg, "QUIET_HOURS", "") or ""
            if qh and within_quiet_hours(qh):
                logging.info("Within QUIET_HOURS %s; suppressing mutates.", qh)
                return
        except Exception:
            return

        enable = self.check_severe_weather()
        # If rules are present, evaluate for potential override
        desired_action = None
        if self._rules:
            desired_action = self._evaluate_rules()
        if desired_action == "NOOP":
            logging.info("Rules requested NOOP; skipping any mutates.")
            return
        if desired_action == "PAUSE":
            enable = False
        elif desired_action == "ENABLE":
            enable = True
        if enable:
            self._enable_flow()
        else:
            self._pause_flow(desired_action)

    # Rules & scheduling helpers
    def _within_quiet_hours(self) -> bool:
        """Deprecated: use utils.time.within_quiet_hours via update_campaign_status."""
        qh = getattr(cfg, "QUIET_HOURS", "") or ""
        return within_quiet_hours(qh)

    def _evaluate_rules(self) -> str | None:
        """Evaluate loaded rules against last alert context; returns ENABLE/PAUSE/NOOP/None."""
        try:
            state = self._load_state()
            last_iso = state.get("last_alert_utc")
            alert_age_min = None
            if last_iso:
                try:
                    last_dt = datetime.fromisoformat(last_iso)
                    alert_age_min = int((datetime.now(timezone.utc) - last_dt).total_seconds() // 60)
                except Exception:
                    alert_age_min = None
            last_severity = None
            area_ids = list(state.get("last_county_fips") or [])
            return eval_rules(
                self._rules,
                severity=last_severity,
                event=None,
                counties_fips=area_ids,
                alert_age_minutes=alert_age_min,
            )
        except Exception:
            return None

    # Enable/Pause flows
    def _maybe_notify_breaker(self, until):
        """Notify once when Ads circuit breaker is open (cooldown controlled)."""
        try:
            if should_notify_breaker("ads", getattr(cfg, "ADS_BREAKER_NOTIFY_COOLDOWN_MIN", 60)):
                self.notifier.notify(
                    subject="LSA Circuit Breaker OPEN",
                    body=(
                        f"Ads API breaker is open until {until or 'unknown'}. Auto-switching to validate-only behavior."
                    ),
                )
                mark_breaker_notified("ads")
        except Exception:
            pass

    def _resolve_area_ids(self) -> list[str]:
        """Resolve target county FIPS from state and optional CANARY_COUNTIES constraint."""
        try:
            state = self._load_state()
            area_ids = list(state.get("last_county_fips") or []) or list(TARGET_COUNTY_FIPS)
            canaries = set(getattr(cfg, "CANARY_COUNTIES", []) or [])
            if canaries:
                filtered: list[str] = []
                for a in area_ids:
                    if a in canaries:
                        filtered.append(a)
                area_ids = filtered
            return area_ids
        except Exception:
            return list(TARGET_COUNTY_FIPS)

    def _enable_flow(self) -> None:
        """Queue ENABLE mutations for mapped or default campaign(s) with dedupe and guards."""
        logging.info("Severe weather detected - enabling campaign")
        area_ids = self._resolve_area_ids()
        if not area_ids:
            logging.info("Enable suppressed: no overlap with CANARY_COUNTIES.")
            return
        try:
            _urm = os.getenv("USE_REGION_MAPPINGS")
            use_mappings = True if _urm is None else (_urm.lower() in {"1", "true", "yes"})
            mappings = get_campaigns_for_areas(area_ids) if use_mappings else []
        except Exception:
            mappings = []
        if mappings:
            ids_only: list[str] = []
            for m in mappings:
                ids_only.append(m[0])
            target_scope = ",".join(sorted(ids_only))
        else:
            target_scope = "default"
        alert_row_id = None
        try:
            last = self._load_state().get("last_alert_utc")
            if last and area_ids:
                ahash = hashlib.sha256(f"nws:*:{last}:{sorted(area_ids)}".encode()).hexdigest()
                alert_row_id = upsert_alert(ahash, "nws", last, ",".join(area_ids), None)
        except Exception:
            pass
        dedupe_window_min = int(os.getenv("ACTION_DEDUPE_MINUTES", "10"))
        try:
            sup = int(getattr(cfg, "ALERT_SUPPRESSION_MINUTES", 0) or 0)
            if sup > 0:
                dedupe_window_min = sup
        except Exception:
            pass
        if is_duplicate_action(alert_row_id, target_scope, "ENABLE", dedupe_window_min):
            logging.info("Deduped ENABLE action for scope=%s within %d minutes", target_scope, dedupe_window_min)
            return
        try:
            open_now, until = is_breaker_open("ads")
        except Exception:
            open_now, until = (False, None)
        if open_now:
            logging.warning("Circuit breaker open until %s; skipping live ENABLE.", until or "unknown")
            self._maybe_notify_breaker(until)
            return
        if mappings:
            for camp_id, cust_id in mappings:
                labels = []
                try:
                    labels = getattr(cfg, "REQUIRED_CAMPAIGN_LABELS", [])
                except Exception:
                    labels = []
                if labels:
                    try:
                        if not self.lsa_client.campaign_has_required_label(
                            cust_id or cfg.CUSTOMER_ID, camp_id or cfg.CAMPAIGN_ID
                        ):
                            logging.info("Skipping campaign %s: missing REQUIRED_CAMPAIGN_LABELS", camp_id)
                            continue
                    except Exception:
                        logging.info("Skipping campaign %s: label check failed", camp_id)
                        continue
                try:
                    enqueue_mutation(alert_row_id, cust_id, camp_id, "status", "ENABLED")
                except Exception:
                    pass
        else:
            cid_env = os.getenv("GOOGLE_ADS_CUSTOMER_ID") or os.getenv("ADS_CUSTOMER_ID")
            camp_env = os.getenv("GOOGLE_ADS_CAMPAIGN_ID") or os.getenv("ADS_CAMPAIGN_ID")
            # Guard against placeholder/missing IDs to avoid endless failures
            if not cid_env or not camp_env or cid_env in {"9999999999"} or camp_env in {"2222222222"}:
                logging.warning(
                    "Skipping enqueue: missing or placeholder GOOGLE_ADS_CUSTOMER_ID / GOOGLE_ADS_CAMPAIGN_ID."
                )
            else:
                enqueue_mutation(alert_row_id, cid_env, camp_env, "status", "ENABLED")
        try:
            record_action_dedupe(alert_row_id, target_scope, "ENABLE")
        except Exception:
            pass
        try:
            update_area_cooldown(area_ids, "ENABLED")
        except Exception:
            pass

    def _pause_flow(self, desired_action: str | None) -> None:
        """Queue a PAUSE mutation for the default campaign with cooldown and guards."""
        logging.info("No severe weather - pausing campaign")
        try:
            area_ids = self._resolve_area_ids()
            if not area_ids:
                logging.info("Pause suppressed: no overlap with CANARY_COUNTIES.")
                return
        except Exception:
            area_ids = list(TARGET_COUNTY_FIPS)
        cooldown_min = int(os.getenv("AREA_COOLDOWN_MINUTES", "30"))
        forced_by_rules = bool(self._rules and desired_action == "PAUSE")
        if not forced_by_rules:
            try:
                blocked, aid = any_area_blocks_pause(area_ids, cooldown_min)
            except Exception:
                blocked, aid = (False, None)
            if blocked:
                logging.info("Pause suppressed by cooldown window (%d min) for area %s", cooldown_min, aid)
                return
        try:
            open_now, until = is_breaker_open("ads")
        except Exception:
            open_now, until = (False, None)
        if open_now:
            logging.warning("Circuit breaker open until %s; skipping live PAUSE.", until or "unknown")
            self._maybe_notify_breaker(until)
            return
        labels = []
        try:
            labels = getattr(cfg, "REQUIRED_CAMPAIGN_LABELS", [])
        except Exception:
            labels = []
        if labels:
            try:
                if not self.lsa_client.campaign_has_required_label(cfg.CUSTOMER_ID, cfg.CAMPAIGN_ID):
                    logging.info("Skipping PAUSE: default campaign missing REQUIRED_CAMPAIGN_LABELS")
                    return
            except Exception:
                logging.info("Skipping PAUSE: label check failed")
                return
        try:
            cid_env = os.getenv("GOOGLE_ADS_CUSTOMER_ID") or os.getenv("ADS_CUSTOMER_ID")
            camp_env = os.getenv("GOOGLE_ADS_CAMPAIGN_ID") or os.getenv("ADS_CAMPAIGN_ID")
            if not cid_env or not camp_env or cid_env in {"9999999999"} or camp_env in {"2222222222"}:
                logging.warning(
                    "Skipping enqueue: missing or placeholder GOOGLE_ADS_CUSTOMER_ID / GOOGLE_ADS_CAMPAIGN_ID."
                )
            else:
                enqueue_mutation(None, cid_env, camp_env, "status", "PAUSED")
        except Exception:
            pass
        try:
            update_area_cooldown(area_ids, "PAUSED")
        except Exception:
            pass
