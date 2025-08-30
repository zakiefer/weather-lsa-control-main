from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx
from cachetools import TTLCache

# A small, shared HTTP client with sane timeouts, connection pooling, and retries.
_CLIENT: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            http2=True,
            timeout=httpx.Timeout(15.0, read=15.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            headers={"User-Agent": "WeatherLSA/1.1 (+streamlit ui)"},
        )
    return _CLIENT


# Two simple caches matching our typical data volatility
_CACHE_2M: TTLCache[str, Any] = TTLCache(maxsize=512, ttl=120)
_CACHE_10M: TTLCache[str, Any] = TTLCache(maxsize=512, ttl=600)

# Light request status tracking for observability in the UI
_STATUS: dict[str, dict[str, Any]] = {}


def _cache_for_ttl(ttl_seconds: int) -> TTLCache:
    return _CACHE_2M if ttl_seconds <= 120 else _CACHE_10M


def _key_for(url: str, params: dict[str, Any] | None) -> str:
    try:
        blob = json.dumps({"u": url, "p": params or {}}, sort_keys=True, separators=(",", ":"))
    except Exception:
        blob = f"{url}|{params!r}"
    # Use a non-crypto hash for cache keys to avoid Bandit B303 (insecure hash). BLAKE2b is fast and safe here.
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    ttl: int = 120,
    max_retries: int = 3,
) -> Any:
    """
    GET a JSON resource with small retry/backoff and a simple TTL cache.

    Arguments:
    - url: Request URL
    - params: Optional query string parameters
    - headers: Optional headers (merged with default UA)
    - timeout: Per-request timeout (seconds)
    - ttl: Cache time-to-live (seconds). <=120 uses the 2-minute cache; otherwise 10-minute cache.
    - max_retries: Number of attempts on transient failures.
    """
    cache = _cache_for_ttl(ttl)
    key = _key_for(url, params)
    if key in cache:
        data = cache[key]
        # record a cache hit status
        _STATUS[key] = {
            "url": url,
            "params": params or {},
            "ok": True,
            "status_code": 200,
            "from_cache": True,
            "last_attempt": time.time(),
            "error": None,
        }
        return data

    client = _get_client()
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)

    # Basic retry with exponential backoff for transient errors
    delay = 0.5
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.get(url, params=params, headers=h, timeout=timeout)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as e:
                    last_err = e
                    break
                cache[key] = data
                _STATUS[key] = {
                    "url": url,
                    "params": params or {},
                    "ok": True,
                    "status_code": resp.status_code,
                    "from_cache": False,
                    "last_attempt": time.time(),
                    "error": None,
                }
                return data
            # Retry on 5xx and 429
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay = min(delay * 2, 3.0)
                continue
            # Non-retryable status
            _STATUS[key] = {
                "url": url,
                "params": params or {},
                "ok": False,
                "status_code": resp.status_code,
                "from_cache": False,
                "last_attempt": time.time(),
                "error": None,
            }
            break
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as e:
            last_err = e
            time.sleep(delay)
            delay = min(delay * 2, 3.0)
            continue
        except Exception as e:
            last_err = e
            break

    # On failure, return None to let callers degrade gracefully
    _STATUS[key] = {
        "url": url,
        "params": params or {},
        "ok": False,
        "status_code": None,
        "from_cache": False,
        "last_attempt": time.time(),
        "error": str(last_err) if last_err else None,
    }
    return None


def get_status_snapshot() -> list[dict[str, Any]]:
    """Return recent request statuses sorted by most recent attempt."""
    return sorted(_STATUS.values(), key=lambda x: x.get("last_attempt") or 0, reverse=True)


def find_latest_status(predicate) -> dict[str, Any] | None:
    """Find the latest status where predicate(url, status_dict) is True."""
    for s in get_status_snapshot():
        try:
            if predicate(str(s.get("url")), s):
                return s
        except Exception:  # nosec B112 - UI helper must not break iteration on bad rows; safe to skip
            continue
    return None


def clear_caches(clear_status: bool = False) -> None:
    """Clear HTTP TTL caches; optionally clear status tracking."""
    try:
        _CACHE_2M.clear()
    except Exception:  # nosec B110 - best-effort cache clear; safe to ignore
        pass
    try:
        _CACHE_10M.clear()
    except Exception:  # nosec B110 - best-effort cache clear; safe to ignore
        pass
    if clear_status:
        try:
            _STATUS.clear()
        except Exception:  # nosec B110 - best-effort status clear; safe to ignore
            pass
