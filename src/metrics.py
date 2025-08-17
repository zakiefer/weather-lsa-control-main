"""Lightweight metrics wrapper with Prometheus-style counters/histograms.

If prometheus_client isn't installed, falls back to no-ops so tests still run.
"""

from __future__ import annotations

import contextlib

try:
    import prometheus_client as _prom  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fallback when lib not installed
    _prom = None  # type: ignore[assignment]


class _NoopCounter:
    def __init__(self, *a, **k):
        pass

    def inc(self, n: float = 1.0):
        return None


class _NoopHistogram:
    def __init__(self, *a, **k):
        pass

    @contextlib.contextmanager
    def time(self):
        yield

    def observe(self, _value: float):
        return None


def _counter(*args, **kwargs):
    if _prom is not None:
        return _prom.Counter(*args, **kwargs)
    return _NoopCounter(*args, **kwargs)


def _histogram(*args, **kwargs):
    if _prom is not None:
        return _prom.Histogram(*args, **kwargs)
    return _NoopHistogram(*args, **kwargs)


# Core metrics
ALERTS_SEEN = _counter("weather_lsa_alerts_seen_total", "Number of triggering alerts seen")
ACTIONS_APPLIED = _counter("weather_lsa_actions_applied_total", "Number of ad actions applied")
API_ERRORS = _counter("weather_lsa_api_errors_total", "Number of upstream API errors (NWS/Ads)")
NOTIFICATIONS_SENT = _counter("weather_lsa_notifications_sent_total", "Number of notifications sent")
ADS_VALIDATE_OK = _counter(
    "weather_lsa_ads_validate_ok_total",
    "Number of Ads validate-only successful checks",
)
ADS_VERIFY_MISMATCH = _counter(
    "weather_lsa_ads_verify_mismatch_total",
    "Number of read-after-write verification mismatches",
)
ADS_ROLLBACKS = _counter(
    "weather_lsa_ads_rollbacks_total",
    "Number of rollbacks performed after verification mismatches",
)

NWS_FETCH_SECONDS = _histogram(
    "weather_lsa_nws_fetch_seconds",
    "Latency of NWS fetch operations in seconds",
)
ADS_MUTATE_SECONDS = _histogram(
    "weather_lsa_ads_mutate_seconds",
    "Latency of Google Ads mutate operations in seconds",
)
ADS_RATE_LIMIT_WAIT_SECONDS = _histogram(
    "weather_lsa_ads_rate_limit_wait_seconds",
    "Time spent waiting on Ads API rate limiter in seconds",
)
ADS_RATE_LIMIT_SLEEPS = _counter(
    "weather_lsa_ads_rate_limit_sleeps_total",
    "Number of sleeps due to Ads API rate limiting",
)
CAP_DEDUPE_SKIPPED = _counter(
    "weather_lsa_cap_dedupe_skipped_total",
    "Number of CAP alerts skipped due to de-duplication (stale/equal effective time)",
)


def start_metrics_server(port: int | None) -> None:
    if not port:
        return
    if _prom is None:  # no-op when client missing
        return
    # Start only once; idempotent best-effort
    try:
        _prom.start_http_server(int(port))
    except Exception:
        pass


def inc_alerts_seen(n: int = 1) -> None:
    try:
        ALERTS_SEEN.inc(n)
    except Exception:
        pass


def inc_actions_applied(n: int = 1) -> None:
    try:
        ACTIONS_APPLIED.inc(n)
    except Exception:
        pass


def inc_api_errors(n: int = 1) -> None:
    try:
        API_ERRORS.inc(n)
    except Exception:
        pass


def inc_notifications_sent(n: int = 1) -> None:
    try:
        NOTIFICATIONS_SENT.inc(n)
    except Exception:
        pass


def inc_ads_validate_ok(n: int = 1) -> None:
    try:
        ADS_VALIDATE_OK.inc(n)
    except Exception:
        pass


def inc_ads_verify_mismatch(n: int = 1) -> None:
    try:
        ADS_VERIFY_MISMATCH.inc(n)
    except Exception:
        pass


def inc_ads_rollbacks(n: int = 1) -> None:
    try:
        ADS_ROLLBACKS.inc(n)
    except Exception:
        pass


def observe_ads_rate_limit_wait(seconds: float) -> None:
    try:
        if seconds and seconds > 0:
            ADS_RATE_LIMIT_WAIT_SECONDS.observe(float(seconds))
            ADS_RATE_LIMIT_SLEEPS.inc(1)
    except Exception:
        pass


def inc_cap_dedupe_skipped(n: int = 1) -> None:
    try:
        CAP_DEDUPE_SKIPPED.inc(n)
    except Exception:
        pass


@contextlib.contextmanager
def time_nws_fetch():
    with NWS_FETCH_SECONDS.time():
        yield


@contextlib.contextmanager
def time_ads_mutate():
    with ADS_MUTATE_SECONDS.time():
        yield
