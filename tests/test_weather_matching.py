import os

from src.weather_monitor import WeatherMonitor


class DummyCreds:
    token = "dummy"
    expired = False
    refresh_token = None


def test_fips_and_name_matching(tmp_path, monkeypatch):
    # Ensure DRY_RUN to avoid network calls
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("FORCE_ALERT", "false")

    # Point logs to a temp dir
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Monkeypatch project root detection in WeatherMonitor by changing CWD
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Prepare a fake alert feature that matches FIPS
        alert_feature = {
            "properties": {
                "event": "Tornado Warning",
                "geocode": {"FIPS6": ["018163"]},  # Vanderburgh, IN -> last 5: 18163 present
            },
            "geometry": None,
        }

        # Monkeypatch requests.get to return our fake alert
        class Resp:
            status_code = 200

            def json(self):
                return {"features": [alert_feature]}

        import src.weather_monitor as wm

        monkeypatch.setattr(wm.requests, "get", lambda *a, **k: Resp())

        monitor = WeatherMonitor(DummyCreds())
        assert monitor.check_severe_weather() is True

        # Now test name fallback by removing FIPS and using areaDesc
        alert_feature["properties"].pop("geocode", None)
        alert_feature["properties"]["areaDesc"] = "Vanderburgh County"
        monitor = WeatherMonitor(DummyCreds())
        assert monitor.check_severe_weather() is True
    finally:
        os.chdir(cwd)
