import pytest


class _Resp:
    def __init__(self, status_code=200, json_payload=None, text="OK"):
        self.status_code = status_code
        self._json = json_payload if json_payload is not None else {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._json


def _gaql_rows_campaign_status(status="ENABLED"):
    return {"results": [{"campaign": {"status": status}}]}


@pytest.fixture
def no_rate_limit(monkeypatch):
    # Disable rate limiter sleeps
    import src.ratelimit as rl

    def _noop(self):
        return None

    monkeypatch.setattr(rl.TokenBucket, "acquire", _noop)


def test_ads_breaker_trips_and_recovers(dummy_creds, monkeypatch, no_rate_limit):
    # Speed up backoff to avoid sleeps
    import src.db as db
    import src.lsa_client as lc

    # Ensure env/guards allow mutate path to run in test
    monkeypatch.setattr(lc, "DEVELOPER_TOKEN", "devtoken", raising=False)
    monkeypatch.setattr(lc, "LOGIN_CUSTOMER_ID", "", raising=False)
    monkeypatch.setattr(lc, "REQUIRE_LOCAL_SERVICES_ONLY", False, raising=False)
    monkeypatch.setattr(lc, "VALIDATE_ONLY", False, raising=False)
    monkeypatch.setattr(lc, "DRY_RUN", False, raising=False)

    monkeypatch.setattr(lc, "ADS_BACKOFF_MAX_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(lc, "ADS_BACKOFF_BASE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(lc, "ADS_BACKOFF_MAX_SLEEP", 0.0, raising=False)
    monkeypatch.setattr(lc, "ADS_BREAKER_THRESHOLD", 2, raising=False)
    monkeypatch.setattr(lc, "ADS_BREAKER_COOLDOWN_MIN", 5, raising=False)

    # Prepare a sequence: initial mutate attempts return 500 to trip breaker
    calls = {"count": 0}

    def fake_post(url, headers=None, json=None):  # noqa: A002
        calls["count"] += 1
        # Any GAQL status check should return OK to proceed to mutate
        if url.endswith(":search"):
            return _Resp(200, _gaql_rows_campaign_status("ENABLED"))
        # Mutate path: first few calls 500 to trigger breaker
        if "/campaigns:mutate" in url and calls["count"] <= 3:
            return _Resp(500, {"error": {"message": "boom"}}, text="boom")
        return _Resp(200, {"results": []})

    monkeypatch.setattr(lc.requests, "post", fake_post)

    client = lc.LSAClient(dummy_creds)

    # First call should fail and contribute to breaker state; second trips it
    ok = client.set_campaign_status("PAUSED", customer_id="1234567890", campaign_id="1111111111", validate_only=False)
    assert ok is False
    # After enough failures, breaker should be open
    open_now, until = db.is_breaker_open("ads")
    assert open_now is True
    assert until is not None

    # Reset breaker by recording a success (simulating a healthy call)
    db.record_breaker_result("ads", ok=True, threshold=2, cooldown_minutes=5)
    open_now2, _ = db.is_breaker_open("ads")
    assert open_now2 is False


def test_burst_alerts_no_duplicate_enqueue(
    weather_monitor_with_tmp_logs, set_nws_alerts, patch_weather_settings, monkeypatch
):
    # Configure filters to accept our synthetic alert
    patch_weather_settings(
        TARGET_COUNTY_FIPS={"18163"},  # Vanderburgh
        TRIGGER_EVENTS={"Severe Thunderstorm Warning"},
        ALLOWED_SEVERITIES={"Severe"},
        ALLOWED_URGENCY={"Immediate"},
        ALLOWED_CERTAINTY={"Observed"},
        QUIET_HOURS="",
        KILL_SWITCH=False,
    )

    # Synthetic alert feature for IN with a fixed CAP/effective pair
    cap_id = "TEST-CAP-123"
    effective = "2025-08-14T00:00:00Z"
    features = [
        {
            "properties": {
                "event": "Severe Thunderstorm Warning",
                "severity": "Severe",
                "urgency": "Immediate",
                "certainty": "Observed",
                "areaDesc": "Vanderburgh County",
                "geocode": {"FIPS6": ["181630"]},
                "id": cap_id,
                "effective": effective,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-87.7, 37.9], [-87.6, 38.0], [-87.5, 37.9], [-87.7, 37.9]]],
            },
        }
    ]

    set_nws_alerts(features)

    mon = weather_monitor_with_tmp_logs

    # Burst: call update multiple times quickly; dedupe should prevent multiple enqueues
    for _ in range(5):
        mon.update_campaign_status()

    # Inspect queue stats
    import src.db as db

    stats = db.get_queue_stats()
    # Only one queued item expected
    assert stats.get("queued", 0) == 1
