#!/usr/bin/env python3
"""
Quick live-ingest verifier for NWS source.
- Uses the app's actual code paths to fetch from api.weather.gov (no mocks).
- Prints per-state feature counts and the final decision from check_severe_weather().
- Exits 0 on success, non-zero on unexpected errors.
"""

import os
import sys
import time

# Ensure src/ is on sys.path for module imports
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)
SRC = os.path.join(PROJECT_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from src.config.settings import STATE_CODES  # type: ignore
from src.metrics import start_metrics_server  # type: ignore
from src.weather_monitor import WeatherMonitor  # type: ignore


def main() -> int:
    # Start a transient metrics server so we can optionally scrape during this run
    port = int(os.getenv("METRICS_PORT", "0") or "0")
    if port:
        start_metrics_server(port)
        # Give metrics a moment to boot
        time.sleep(0.2)

    # Instantiate monitor with no Ads creds; ingest does not require them
    mon = WeatherMonitor(credentials=None)  # type: ignore[arg-type]

    # Per-state fetch using the app code (live HTTP to api.weather.gov)
    total = 0
    for st in STATE_CODES:
        feats = mon._fetch_alerts_for_state(st)  # type: ignore[attr-defined]
        cnt = len(feats or [])
        total += cnt
        print(f"NWS features for {st}: {cnt}")

    # Full decision path (still live ingest inside)
    decision = mon.check_severe_weather()
    print(f"check_severe_weather => {decision}")
    # Small grace period if metrics are enabled
    if port:
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
