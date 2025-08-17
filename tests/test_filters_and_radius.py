import os  # noqa: F401


def test_severity_filter_blocks_non_matching(
    weather_monitor_with_tmp_logs,
    set_nws_alerts,
    patch_weather_settings,
    monkeypatch,
):
    # Only allow 'Severe' severity; provide an alert with 'Moderate' to ensure it's ignored
    monkeypatch.setenv("DRY_RUN", "true")
    patch_weather_settings(
        TRIGGER_EVENTS={"Tornado Warning"},
        ALLOWED_SEVERITIES={"Severe"},
        ALLOWED_URGENCY=set(),
        ALLOWED_CERTAINTY=set(),
    )

    alert = {
        "properties": {
            "event": "Tornado Warning",
            "severity": "Moderate",
            "geocode": {"FIPS6": ["018163"]},  # Vanderburgh, IN county FIPS 18163
        },
        "geometry": None,
    }

    set_nws_alerts([alert])

    assert weather_monitor_with_tmp_logs.check_severe_weather() is False


def test_radius_blocks_far_alert(weather_monitor_with_tmp_logs, set_nws_alerts, patch_weather_settings, monkeypatch):
    # Enable radius and use a polygon centroid far away from center
    monkeypatch.setenv("DRY_RUN", "true")
    patch_weather_settings(
        TRIGGER_EVENTS={"Tornado Warning"},
        ALLOWED_SEVERITIES=set(),
        ALLOWED_URGENCY=set(),
        ALLOWED_CERTAINTY=set(),
        CENTER_LAT=37.975,  # Evansville area approx
        CENTER_LON=-87.555,
        MAX_DISTANCE_MI=5.0,
    )

    # Construct a simple square polygon with centroid near New York (far > 5mi)
    far_centroid = (-73.9857, 40.7484)  # lon, lat for NYC-ish
    ring = [
        [far_centroid[0] - 0.01, far_centroid[1] - 0.01],
        [far_centroid[0] - 0.01, far_centroid[1] + 0.01],
        [far_centroid[0] + 0.01, far_centroid[1] + 0.01],
        [far_centroid[0] + 0.01, far_centroid[1] - 0.01],
        [far_centroid[0] - 0.01, far_centroid[1] - 0.01],
    ]
    alert = {
        "properties": {
            "event": "Tornado Warning",
            "geocode": {"FIPS6": ["018163"]},
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }

    set_nws_alerts([alert])

    assert weather_monitor_with_tmp_logs.check_severe_weather() is False


def test_radius_allows_near_alert(weather_monitor_with_tmp_logs, set_nws_alerts, patch_weather_settings, monkeypatch):
    # Enable radius and use a polygon centroid near the center
    monkeypatch.setenv("DRY_RUN", "true")
    clat = 37.975
    clon = -87.555
    patch_weather_settings(
        TRIGGER_EVENTS={"Tornado Warning"},
        ALLOWED_SEVERITIES=set(),
        ALLOWED_URGENCY=set(),
        ALLOWED_CERTAINTY=set(),
        CENTER_LAT=clat,
        CENTER_LON=clon,
        MAX_DISTANCE_MI=50.0,
    )

    near_centroid = (clon + 0.01, clat + 0.01)  # lon, lat near center
    ring = [
        [near_centroid[0] - 0.01, near_centroid[1] - 0.01],
        [near_centroid[0] - 0.01, near_centroid[1] + 0.01],
        [near_centroid[0] + 0.01, near_centroid[1] + 0.01],
        [near_centroid[0] + 0.01, near_centroid[1] - 0.01],
        [near_centroid[0] - 0.01, near_centroid[1] - 0.01],
    ]
    alert = {
        "properties": {
            "event": "Tornado Warning",
            "geocode": {"FIPS6": ["018163"]},
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }

    set_nws_alerts([alert])

    assert weather_monitor_with_tmp_logs.check_severe_weather() is True


def test_urgency_and_certainty_filters(
    weather_monitor_with_tmp_logs,
    set_nws_alerts,
    patch_weather_settings,
    monkeypatch,
):
    # Only allow specific urgency/certainty; provide non-matching to block; matching to allow
    monkeypatch.setenv("DRY_RUN", "true")
    patch_weather_settings(
        TRIGGER_EVENTS={"Severe Thunderstorm Warning"},
        ALLOWED_SEVERITIES=set(),
        ALLOWED_URGENCY={"Immediate"},
        ALLOWED_CERTAINTY={"Observed"},
    )

    base = {
        "properties": {
            "event": "Severe Thunderstorm Warning",
            "geocode": {"FIPS6": ["018163"]},
        },
        "geometry": None,
    }

    # Non-matching urgency
    alert1 = {**base, "properties": {**base["properties"], "urgency": "Expected", "certainty": "Observed"}}
    set_nws_alerts([alert1])
    assert weather_monitor_with_tmp_logs.check_severe_weather() is False

    # Non-matching certainty
    alert2 = {**base, "properties": {**base["properties"], "urgency": "Immediate", "certainty": "Likely"}}
    set_nws_alerts([alert2])
    assert weather_monitor_with_tmp_logs.check_severe_weather() is False

    # Matching both
    alert3 = {**base, "properties": {**base["properties"], "urgency": "Immediate", "certainty": "Observed"}}
    set_nws_alerts([alert3])
    assert weather_monitor_with_tmp_logs.check_severe_weather() is True


def test_storm_hold_keeps_enabled_after_alert(
    weather_monitor_with_tmp_logs,
    set_nws_alerts,
    patch_weather_settings,
    monkeypatch,
):
    # Trigger an alert once, then with no alerts ensure hold window returns True
    monkeypatch.setenv("DRY_RUN", "true")
    patch_weather_settings(
        TRIGGER_EVENTS={"Tornado Warning"},
        ALLOWED_SEVERITIES=set(),
        ALLOWED_URGENCY=set(),
        ALLOWED_CERTAINTY=set(),
    )

    alert = {
        "properties": {
            "event": "Tornado Warning",
            "geocode": {"FIPS6": ["018163"]},
        },
        "geometry": None,
    }
    set_nws_alerts([alert])
    assert weather_monitor_with_tmp_logs.check_severe_weather() is True

    # Now no alerts, but within hold window should still be True
    set_nws_alerts([])
    assert weather_monitor_with_tmp_logs.check_severe_weather() is True
