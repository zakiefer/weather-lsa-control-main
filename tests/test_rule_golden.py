import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

import pytest

# Constants for golden
CID = "0000000000"
CAMP = "1111111111"


class PostEntry(TypedDict, total=False):
    method: str
    urlSuffix: str
    query: str
    body: dict[str, Any] | None


def _fake_ads_post_factory(calls: list[PostEntry]):
    class Resp:
        def __init__(self, ok=True, status_code=200, data=None, text=""):
            self.ok = ok
            self.status_code = status_code
            self._data = data or {}
            self.text = text

        def json(self):
            return self._data

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(f"HTTP {self.status_code}")

    def _post(url, headers=None, json=None):  # noqa: A002
        # capture minimal snapshot
        entry: PostEntry = {"method": "POST"}
        # Only store suffix to be resilient to version/base
        try:
            # split after version path
            parts = url.split("/customers/", 1)
            if len(parts) == 2:
                suffix = "/customers/" + parts[1]
                # Normalize customer to expected CID for stable golden
                login_cust = ""
                try:
                    if isinstance(headers, dict):
                        login_cust = str(headers.get("login-customer-id", ""))
                except Exception:
                    login_cust = ""
                suffix = suffix.replace(f"/customers/{login_cust}", f"/customers/{CID}")
                suffix = suffix.replace(f"/customers/{CID}", f"/customers/{CID}")
            else:
                suffix = url
            entry["urlSuffix"] = suffix.split(f"/customers/{CID}", 1)[-1]
        except Exception:
            entry["urlSuffix"] = url
        # GAQL or mutate body
        if json and "query" in json:
            entry["query"] = json["query"]
            # Build response for GAQL queries
            q = json["query"]
            if "advertising_channel_type" in q:
                data = {"results": [{"campaign": {"id": CAMP, "advertisingChannelType": "LOCAL_SERVICES"}}]}
                calls.append(entry)
                return Resp(ok=True, data=data)
            if "campaign.status" in q:
                # Return status equal to target based on last mutate call stored in calls
                target: str | None = None
                for e in reversed(calls):
                    if e.get("urlSuffix", "").startswith("/campaigns:mutate"):
                        try:
                            body = e.get("body")
                            if isinstance(body, dict):
                                ops = body.get("operations")
                                if isinstance(ops, list) and ops:
                                    upd = ops[0].get("update") if isinstance(ops[0], dict) else None
                                    if isinstance(upd, dict):
                                        val = upd.get("status")
                                        if isinstance(val, str):
                                            target = val
                        except Exception:
                            pass
                        break
                if target is None:
                    target = "ENABLED"
                data = {"results": [{"campaign": {"status": target}}]}
                calls.append(entry)
                return Resp(ok=True, data=data)
        else:
            # Ensure body is a plain dict for stable typing in golden assertions
            try:
                entry["body"] = dict(json) if isinstance(json, dict) else json
            except Exception:
                entry["body"] = json
            calls.append(entry)
            return Resp(ok=True, data={})

    return _post


@pytest.fixture()
def golden_dir():
    return os.path.join(os.path.dirname(__file__), "golden")


def _write_rules(tmp_path, rules):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return str(p)


def _setup_env(monkeypatch):
    # Ensure settings for Ads client
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dummy")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", CID)
    monkeypatch.setenv("GOOGLE_ADS_CAMPAIGN_ID", CAMP)
    monkeypatch.setenv("REQUIRE_LOCAL_SERVICES_ONLY", "true")
    monkeypatch.setenv("GOOGLE_ADS_VALIDATE_ONLY", "true")
    # Quiet down metrics and health
    monkeypatch.delenv("METRICS_PORT", raising=False)
    monkeypatch.delenv("HEALTH_PORT", raising=False)


def _clear_queue():
    from src.db import ensure_schema, get_conn

    ensure_schema()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM mutation_queue")
        cur.execute("DELETE FROM action_dedupe")
        cur.execute("DELETE FROM circuit_breaker")
        conn.commit()
    finally:
        conn.close()


def _run_monitor_and_worker(monkeypatch, dummy_creds, rules_file, alerts):
    # Patch weather API to return given alerts
    import src.weather_monitor as wm

    class Resp:
        status_code = 200

        def json(self):
            return {"features": alerts}

    monkeypatch.setattr(wm.requests, "get", lambda *a, **k: Resp())
    # Patch rules file path
    monkeypatch.setattr(wm, "RULES_FILE", rules_file, raising=False)
    # Narrow target to a single FIPS for stability
    monkeypatch.setattr(wm, "TARGET_COUNTY_FIPS", {"18163"}, raising=False)
    # Run monitor and worker
    from src.weather_monitor import WeatherMonitor
    from src.worker import drain_queue

    mon = WeatherMonitor(dummy_creds)
    mon.update_campaign_status()
    # Capture Ads calls
    calls = []
    import src.lsa_client as lc

    monkeypatch.setattr(lc.requests, "post", _fake_ads_post_factory(calls))
    drain_queue(dummy_creds)
    return calls


def test_rule_enable_golden(tmp_path, monkeypatch, dummy_creds, golden_dir):
    _setup_env(monkeypatch)
    _clear_queue()
    # Rule: ENABLE on Severe
    rules_file = _write_rules(tmp_path, [{"name": "sev_enable", "severities": ["Severe"], "action": "ENABLE"}])
    # CAP alert triggering Severe in FIPS 18163
    alert = {
        "properties": {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "geocode": {"FIPS6": ["018163"]},
            "effective": datetime.now(timezone.utc).isoformat(),
        },
        "geometry": None,
    }
    calls = _run_monitor_and_worker(monkeypatch, dummy_creds, rules_file, [alert])
    expected = json.load(open(os.path.join(golden_dir, "enable.json"), encoding="utf-8"))
    assert calls == expected


def test_rule_pause_golden(tmp_path, monkeypatch, dummy_creds, golden_dir):
    _setup_env(monkeypatch)
    _clear_queue()
    # Rule: PAUSE after 60m
    rules_file = _write_rules(tmp_path, [{"name": "cooldown_pause", "min_duration": "60m", "action": "PAUSE"}])
    # No active alerts now; but set last alert older than 60 minutes for pause
    # We can seed the state by first running a prior alert, then run with no alerts
    prior_alert = {
        "properties": {
            "event": "Tornado Warning",
            "severity": "Severe",
            "geocode": {"FIPS6": ["018163"]},
            "effective": (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat(),
        },
        "geometry": None,
    }
    # First run to record state
    _run_monitor_and_worker(monkeypatch, dummy_creds, rules_file, [prior_alert])
    # Now run with no alerts to trigger PAUSE via rules
    calls = _run_monitor_and_worker(monkeypatch, dummy_creds, rules_file, [])
    expected = json.load(open(os.path.join(golden_dir, "pause.json"), encoding="utf-8"))
    assert calls == expected


def test_rule_noop_golden(tmp_path, monkeypatch, dummy_creds, golden_dir):
    _setup_env(monkeypatch)
    _clear_queue()
    # Rule: NOOP for county 18163
    rules_file = _write_rules(tmp_path, [{"name": "skip_this_county", "counties": ["18163"], "action": "NOOP"}])
    alert = {
        "properties": {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "geocode": {"FIPS6": ["018163"]},
            "effective": datetime.now(timezone.utc).isoformat(),
        },
        "geometry": None,
    }
    calls = _run_monitor_and_worker(monkeypatch, dummy_creds, rules_file, [alert])
    expected = json.load(open(os.path.join(golden_dir, "noop.json"), encoding="utf-8"))
    assert calls == expected
