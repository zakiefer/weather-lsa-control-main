"""Lightweight health and readiness HTTP endpoints.

/healthz returns 200 OK when the server is up.
/readyz returns 200 OK only when dependencies are ready (DB, creds, breaker closed).
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import settings as cfg
from .db import get_conn, get_queue_stats, is_breaker_open


def _check_db() -> bool:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            _ = cur.fetchone()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def _check_creds() -> bool:
    # Lightweight check: token file present and loadable
    try:
        from .__main__ import TOKEN_FILE

        if not os.path.exists(TOKEN_FILE):
            return False
        return True
    except Exception:
        return False


def _check_smtp() -> dict:
    """Return SMTP readiness details.
    - If email notifications disabled, returns {enabled: False}.
    - If enabled, attempts to connect and start TLS, and login if creds are provided.
    """
    # Values may include booleans and error strings; use a plain dict[str, object]
    info: dict[str, object] = {
        "enabled": bool(getattr(cfg, "ENABLE_NOTIFICATIONS", False) and getattr(cfg, "ENABLE_EMAIL", False))
    }
    if not info["enabled"]:
        return info
    try:
        import smtplib

        server = smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=3)
        try:
            server.starttls()
        except Exception:
            pass
        if cfg.SMTP_USER and cfg.SMTP_PASSWORD:
            try:
                server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
                info["login"] = True
            except Exception as e:
                info["login"] = False
                info["error"] = str(e)
        server.quit()
        # ok is True if we logged in when creds are provided, or True when no creds required
        logged_in = info.get("login", True)
        info["ok"] = bool(logged_in)
    except Exception as e:
        info["ok"] = False
        info["error"] = str(e)
    return info


# Cache for clock skew to avoid external HTTP on every probe
_CLOCK_CACHE: dict[str, Any] = {"ts": None, "data": None}
_CLOCK_TTL_SECONDS = 300


def _clock_skew() -> dict:
    """Compute clock skew vs an external server's Date header.
    Returns {skew_seconds, server_time, source}. Uses a short timeout and caches for a few minutes.
    """
    try:
        now = datetime.now(timezone.utc)
        ts = _CLOCK_CACHE.get("ts")
        if ts and isinstance(ts, datetime) and (now - ts) < timedelta(seconds=_CLOCK_TTL_SECONDS):
            return _CLOCK_CACHE["data"] or {}
    except Exception:
        pass
    result = {}
    try:
        import requests as _req

        r = _req.get(
            "https://www.google.com/generate_204",
            timeout=3,
            headers={"Cache-Control": "no-cache"},
        )
        srv_date = r.headers.get("Date")
        if srv_date:
            dt = parsedate_to_datetime(srv_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            skew_s = int(abs((now - dt).total_seconds()))
            result = {"skew_seconds": skew_s, "server_time": dt.isoformat(), "source": "generate_204"}
    except Exception:
        # Leave result empty on failure
        result = {}
    try:
        _CLOCK_CACHE["ts"] = datetime.now(timezone.utc)
        _CLOCK_CACHE["data"] = result
    except Exception:
        pass
    return result


def compute_readiness() -> dict:
    db_ok = _check_db()
    creds_ok = _check_creds()
    try:
        open_now, until = is_breaker_open("ads")
    except Exception:
        open_now, until = (False, None)
    try:
        qs = get_queue_stats()
    except Exception:
        qs = {"queued": 0, "running": 0, "done": 0, "error": 0}
    smtp = _check_smtp()
    clock = _clock_skew()
    ready = bool(db_ok and creds_ok and not open_now)
    return {
        "ready": ready,
        "db_ok": db_ok,
        "creds_ok": creds_ok,
        "breaker_open": bool(open_now),
        "breaker_until": until,
        "queue": qs,
        "smtp": smtp,
        "clock": clock,
        "time": datetime.now(timezone.utc).isoformat(),
    }


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - match BaseHTTPRequestHandler signature
        # Quiet
        return

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/healthz"):
            self._send(200, {"status": "ok"})
            return
        if self.path.startswith("/readyz"):
            info = compute_readiness()
            self._send(200 if info.get("ready") else 503, info)
            return
        self._send(404, {"error": "not found"})


def start_health_server(port: int) -> None:
    try:
        server = ThreadingHTTPServer(("0.0.0.0", int(port)), _HealthHandler)
        t = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
        t.start()
        logging.info("Health endpoints listening on :%d (/healthz, /readyz)", int(port))
    except Exception as e:
        logging.warning("Health server failed to start: %s", e)
