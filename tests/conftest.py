import os

import pytest


class DummyCreds:
    token = "dummy"
    expired = False
    refresh_token = None


@pytest.fixture
def dummy_creds():
    return DummyCreds()


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Use a fresh SQLite DB per test to avoid cross-test leakage (e.g., region mappings).

    We override src.db.DB_PATH to a temp file and ensure schema before each test.
    """
    import src.db as db

    db.DB_PATH = str(tmp_path / "test.db")
    db.ensure_schema()


@pytest.fixture
def tmp_logs_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def weather_monitor_with_tmp_logs(dummy_creds, tmp_logs_dir, monkeypatch):
    # Create a WeatherMonitor and redirect its log/state to a temp directory
    from src.weather_monitor import WeatherMonitor

    m = WeatherMonitor(dummy_creds)
    m.log_dir = str(tmp_logs_dir)
    m.state_file = os.path.join(str(tmp_logs_dir), "storm_state.json")
    # Ensure a clean state file for each test
    if os.path.exists(m.state_file):
        os.remove(m.state_file)
    return m


@pytest.fixture
def set_nws_alerts(monkeypatch):
    # Patch requests.get inside src.weather_monitor to return a custom alerts payload
    import src.weather_monitor as wm

    def _set(features):
        class Resp:
            status_code = 200

            def json(self):
                return {"features": features}

        monkeypatch.setattr(wm.requests, "get", lambda *a, **k: Resp())

    return _set


@pytest.fixture
def patch_weather_settings(monkeypatch):
    # Patch module-level settings imported by weather_monitor at import time
    import src.weather_monitor as wm

    def _patch(**kwargs):
        for k, v in kwargs.items():
            monkeypatch.setattr(wm, k, v, raising=False)

    return _patch
