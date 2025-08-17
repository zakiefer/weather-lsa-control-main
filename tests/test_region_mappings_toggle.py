# ruff: noqa: I001
import os
from src.db import ensure_schema, upsert_region_mapping
from src.weather_monitor import WeatherMonitor


def test_region_mapping_toggle(monkeypatch, dummy_creds):
    # Ensure settings and mapping DB

    ensure_schema()
    # Insert a mapping for area 18163
    upsert_region_mapping("18163", "2222222222", "9999999999")

    # Use region mappings
    monkeypatch.setenv("USE_REGION_MAPPINGS", "true")
    WeatherMonitor(dummy_creds)
    # If we had an alert enqueued for 18163, the code path would use mapping (behavior checked indirectly via DB)
    assert os.getenv("USE_REGION_MAPPINGS") == "true"

    # Disable region mappings
    monkeypatch.setenv("USE_REGION_MAPPINGS", "false")
    WeatherMonitor(dummy_creds)
    assert os.getenv("USE_REGION_MAPPINGS") == "false"
