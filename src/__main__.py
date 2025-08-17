"""CLI entrypoint for Weather LSA Control.

Provides:
- One-shot commands (doctor, status, audit export, mappings).
- Worker queue utilities (queue-status, drain-queue via worker).
- Scheduler for periodic weather checks and queue draining.

Notes for maintainers:
- Dispatcher pattern: main() is a thin router that delegates command branches to
    small helper functions named `_cmd_<name>(args)`. Keep helpers pure and
    side-effect-aware with clear logging; avoid behavior changes in refactors.
- When adding a new command, prefer creating a helper and delegating from
    main() to reduce complexity and improve testability.

Health and metrics can be enabled via HEALTH_PORT and METRICS_PORT.
"""

import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config.settings import ADS_HTTP_TIMEOUT_SECONDS
from .metrics import start_metrics_server

# Internal utilities
from .observability import capture_error, init_sentry
from .secrets_manager import get_secret, masked, migrate_from_files_and_env
from .tracing import init_otel

# Paths and constants
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
SECRETS_DIR = os.path.join(PROJECT_ROOT, "secrets")
STANDARD_CLIENT_SECRET_FILE = os.path.join(SECRETS_DIR, "client_secret.json")
DEFAULT_CLIENT_SECRET_FILE = STANDARD_CLIENT_SECRET_FILE
TOKEN_FILE = os.path.join(SECRETS_DIR, "token.json")
SCOPES = ["https://www.googleapis.com/auth/adwords"]

# Logging: rotating file + console
os.makedirs(LOG_DIR, exist_ok=True)
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
_file_handler = RotatingFileHandler(os.path.join(LOG_DIR, "weather_monitor.log"), maxBytes=1_000_000, backupCount=3)
_file_handler.setFormatter(_fmt)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_fmt)
_root_logger.handlers = [_file_handler, _stream_handler]


def _resolve_client_secret_file() -> Optional[str]:
    # Prefer standardized secrets/client_secret.json
    if os.path.exists(STANDARD_CLIENT_SECRET_FILE):
        return STANDARD_CLIENT_SECRET_FILE
    # Fallbacks for legacy locations
    if os.path.exists(DEFAULT_CLIENT_SECRET_FILE):
        return DEFAULT_CLIENT_SECRET_FILE
    for name in os.listdir(SECRETS_DIR):
        if name.startswith("client_secret") and name.endswith(".json"):
            return os.path.join(SECRETS_DIR, name)
    for name in os.listdir(CURRENT_DIR):
        if name.startswith("client_secret") and name.endswith(".json"):
            return os.path.join(CURRENT_DIR, name)
    for name in os.listdir(PROJECT_ROOT):
        if name.startswith("client_secret") and name.endswith(".json"):
            return os.path.join(PROJECT_ROOT, name)
    return None


def get_credentials():
    # Try to load cached credentials first
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Let google-auth refresh automatically when used
            pass
        else:
            # Start browser OAuth flow
            client_secret_file = _resolve_client_secret_file()
            if not client_secret_file:
                raise FileNotFoundError("No client secret JSON found. Place client_secret.json under secrets/.")
            logging.info(f"Using OAuth client file: {os.path.basename(client_secret_file)}")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes=SCOPES)
            creds = flow.run_local_server(port=0)
            # Persist credentials for reuse
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

    return creds


def parse_args():
    parser = argparse.ArgumentParser(description="Weather-based LSA control")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Ads API; just log requests")
    parser.add_argument("--validate-only", action="store_true", help="Use validateOnly on mutate calls")
    parser.add_argument("--list-campaigns", action="store_true", help="List campaigns and exit")
    parser.add_argument(
        "--create-test-account", action="store_true", help="Create a test account under the manager and exit"
    )
    parser.add_argument("--force-alert", action="store_true", help="Force a simulated alert")
    parser.add_argument("--force-event", type=str, default=None, help="Event name when forcing an alert")
    parser.add_argument("--center-lat", type=float, default=None, help="Optional radius center latitude")
    parser.add_argument("--center-lon", type=float, default=None, help="Optional radius center longitude")
    parser.add_argument("--max-distance-mi", type=float, default=None, help="Radius in miles (enable >0)")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize project: ensure folders, copy .env.example to .env, and validate secrets",
    )
    parser.add_argument("--doctor", action="store_true", help="Validate env, IDs, and secrets; no network calls")
    parser.add_argument("--clear-hold", action="store_true", help="Clear storm hold (forget last alert time)")
    parser.add_argument("--log-level", type=str, default=None, help="Log level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--json-logs", action="store_true", help="Emit logs in single-line JSON structure")
    parser.add_argument("--doctor-live", action="store_true", help="Preflight checks for running without --dry-run")
    parser.add_argument(
        "--doctor-ads", action="store_true", help="Diagnose Google Ads API connectivity and permissions"
    )
    parser.add_argument(
        "--doctor-ads-canary", action="store_true", help="Probe the canary Ads API version for readiness"
    )
    parser.add_argument(
        "--lsa-only", action="store_true", help="Only allow updates to Local Services campaigns (safety guard)"
    )
    parser.add_argument("--status", action="store_true", help="Show campaign status and storm-hold info, then exit")
    parser.add_argument("--status-json", action="store_true", help="Emit status as JSON (no interactive auth)")
    parser.add_argument(
        "--purge-placeholder-queue", action="store_true", help="Delete queued mutations with placeholder IDs"
    )
    parser.add_argument("--export-audit", action="store_true", help="Export last 7 days audit logs (JSONL by default)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to include in --export-audit")
    parser.add_argument(
        "--format", type=str, default="jsonl", choices=["jsonl", "csv"], help="Export format for --export-audit"
    )
    # LSA reporting exports
    parser.add_argument("--lsa-export-leads", action="store_true", help="Export LSA detailed leads for a date range")
    parser.add_argument(
        "--lsa-export-accounts", action="store_true", help="Export LSA account aggregates for a date range"
    )
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD) for LSA reports")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD) for LSA reports")
    parser.add_argument("--output", type=str, default=None, help="Output file path (defaults by type and dates)")
    parser.add_argument(
        "--lsa-format", type=str, default="json", choices=["json", "csv"], help="Format for LSA report exports"
    )
    parser.add_argument(
        "--lsa-diagnose", action="store_true", help="Diagnose LSA manager/customer IDs and suggest settings"
    )
    parser.add_argument(
        "--lsa-ids", action="store_true", help="List accessible Ads customer IDs and linked LSA accountIds"
    )
    parser.add_argument("--lsa-configure", action="store_true", help="Auto-discover MCC and LSA account, write to .env")
    # Region mapping management
    parser.add_argument(
        "--map-region",
        nargs=2,
        metavar=("AREA_FIPS", "CAMPAIGN_ID"),
        help="Upsert a region (area FIPS) to campaign mapping",
    )
    parser.add_argument(
        "--map-customer", type=str, default=None, help="Optional customer ID to store with --map-region"
    )
    parser.add_argument("--unmap-region", type=str, default=None, help="Delete a region mapping by area FIPS")
    parser.add_argument("--list-regions", action="store_true", help="List all region mappings")
    parser.add_argument("--notify-test", action="store_true", help="Send a test email notification and exit")
    parser.add_argument(
        "--send-test-email", action="store_true", help="Send a test email (email channel only) and exit"
    )
    parser.add_argument("--drain-queue", action="store_true", help="Drain queued mutations sequentially and exit")
    parser.add_argument("--queue-status", action="store_true", help="Show mutation queue stats and exit")
    parser.add_argument("--caps", action="store_true", help="List latest CAP alerts and exit")
    parser.add_argument("--caps-limit", type=int, default=25, help="Max number of CAP alerts to list")
    # Rules tooling
    parser.add_argument(
        "--rules-dry-run", action="store_true", help="Simulate rules decisions against recent CAP alerts and exit"
    )
    parser.add_argument(
        "--rules-file", type=str, default=None, help="Path to rules YAML/JSON (overrides configured RULES_FILE)"
    )
    parser.add_argument(
        "--rules-dry-run-limit", type=int, default=25, help="Max number of CAP alerts to simulate in --rules-dry-run"
    )
    parser.add_argument(
        "--scheduler", action="store_true", help="Run periodic monitor and queue drain jobs (APScheduler)"
    )
    # Canary/version management
    parser.add_argument(
        "--promote-canary", action="store_true", help="Promote API_VERSION to API_VERSION_CANARY (persisted override)"
    )
    parser.add_argument(
        "--demote-canary", action="store_true", help="Remove API version override and revert to default API_VERSION"
    )
    parser.add_argument(
        "--show-version", action="store_true", help="Show effective Ads API version in use (with any overrides)"
    )
    parser.add_argument(
        "--promote-if-healthy",
        action="store_true",
        help="Probe canary; if OK, promote API version override and audit it",
    )
    # Region mappings toggle
    rm = parser.add_mutually_exclusive_group()
    rm.add_argument(
        "--use-region-mappings",
        action="store_true",
        help="Use DB region-to-campaign mappings when enqueuing actions (default)",
    )
    rm.add_argument(
        "--no-region-mappings", action="store_true", help="Ignore mappings and use default GOOGLE_ADS_* IDs for actions"
    )
    return parser.parse_args()


def apply_env_overrides(args):
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
    if args.validate_only:
        os.environ["GOOGLE_ADS_VALIDATE_ONLY"] = "true"
    if args.list_campaigns:
        os.environ["LIST_CAMPAIGNS"] = "true"
    if args.create_test_account:
        os.environ["CREATE_TEST_ACCOUNT"] = "true"
    if args.force_alert:
        os.environ["FORCE_ALERT"] = "true"
    if args.force_event is not None:
        os.environ["FORCE_EVENT"] = args.force_event
    if args.center_lat is not None:
        os.environ["CENTER_LAT"] = str(args.center_lat)
    if args.center_lon is not None:
        os.environ["CENTER_LON"] = str(args.center_lon)
    if args.max_distance_mi is not None:
        os.environ["MAX_DISTANCE_MI"] = str(args.max_distance_mi)
    if getattr(args, "lsa_only", False):
        os.environ["REQUIRE_LOCAL_SERVICES_ONLY"] = "true"
    # Region mappings toggle
    if getattr(args, "use_region_mappings", False):
        os.environ["USE_REGION_MAPPINGS"] = "true"
    if getattr(args, "no_region_mappings", False):
        os.environ["USE_REGION_MAPPINGS"] = "false"


def perform_init():
    # Ensure directories exist
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(SECRETS_DIR, exist_ok=True)
    # Create .env from example if needed
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env_example = os.path.join(PROJECT_ROOT, ".env.example")
    if not os.path.exists(env_path) and os.path.exists(env_example):
        try:
            with open(env_example, "r") as src, open(env_path, "w") as dst:
                dst.write(src.read())
            logging.info("Created .env from .env.example")
        except Exception as e:
            logging.warning(f"Failed to create .env from .env.example: {e}")
    # Check for client secret
    secret_file = _resolve_client_secret_file()
    if secret_file and os.path.abspath(secret_file) == os.path.abspath(STANDARD_CLIENT_SECRET_FILE):
        logging.info("Client secret present at secrets/client_secret.json")
    else:
        logging.warning("Client secret not found at secrets/client_secret.json. Place it there before auth.")


def perform_doctor():
    import re

    from .config import settings as cfg

    problems = []
    warnings = []

    # Secrets
    if not os.path.exists(STANDARD_CLIENT_SECRET_FILE):
        problems.append("Missing secrets/client_secret.json")
    if not os.path.exists(TOKEN_FILE):
        warnings.append("No secrets/token.json (run auth once: python -m src)")

    # IDs and tokens (from settings so aliases are honored)
    if not cfg.DEVELOPER_TOKEN:
        problems.append("Missing GOOGLE_ADS_DEVELOPER_TOKEN or ADS_DEV_TOKEN")
    id_re = re.compile(r"^\d{6,}$")
    if not cfg.CUSTOMER_ID:
        problems.append("Missing GOOGLE_ADS_CUSTOMER_ID or ADS_CUSTOMER_ID")
    elif not id_re.match(cfg.CUSTOMER_ID):
        problems.append("GOOGLE_ADS_CUSTOMER_ID must be digits only, no dashes")
    if not cfg.CAMPAIGN_ID:
        problems.append("Missing GOOGLE_ADS_CAMPAIGN_ID or ADS_CAMPAIGN_ID")
    elif not id_re.match(cfg.CAMPAIGN_ID):
        problems.append("GOOGLE_ADS_CAMPAIGN_ID must be digits only")
    if cfg.LOGIN_CUSTOMER_ID and not id_re.match(cfg.LOGIN_CUSTOMER_ID):
        problems.append("GOOGLE_ADS_LOGIN_CUSTOMER_ID must be digits only if set")

    # API version
    if not cfg.API_VERSION.startswith("v"):
        warnings.append(f"Unusual API version: {cfg.API_VERSION}")

    # Directories
    if not os.path.isdir(LOG_DIR):
        warnings.append("logs/ directory will be created on run")
    if not os.path.isdir(SECRETS_DIR):
        problems.append("Missing secrets/ directory")

    # Report
    if problems:
        logging.error("Doctor: FAIL")
        for p in problems:
            logging.error("- %s", p)
    else:
        logging.info("Doctor: PASS (no blocking issues detected)")
    for w in warnings:
        logging.warning("- %s", w)

    # Extra Google Ads access hint: test-only developer tokens cannot access non-test accounts
    if cfg.DEVELOPER_TOKEN and cfg.CUSTOMER_ID:
        logging.warning(
            "- Reminder: If your developer token is test-only, API calls to non-test accounts will fail with DEVELOPER_TOKEN_NOT_APPROVED. Use TEST accounts or request Standard Access."
        )

    # Notification checks (email-only)
    if getattr(cfg, "ENABLE_NOTIFICATIONS", False):
        email_ok = bool(cfg.ENABLE_EMAIL and cfg.SMTP_HOST and cfg.EMAIL_FROM and cfg.EMAIL_TO)
        if not email_ok:
            logging.warning("- Email notifications enabled but SMTP settings or recipients are missing")


def _cmd_status_json(args) -> None:
    try:
        import json as _json
        from datetime import datetime, timezone

        from .db import (
            ensure_schema,
            get_config_value,
            get_queue_stats,
            is_breaker_open,
            list_area_cooldowns,
            list_queued_mutations,
            list_recent_errors,
            summarize_error_codes,
        )
        from .weather_monitor import WeatherMonitor

        ensure_schema()
        payload: dict = {"time": datetime.now(timezone.utc).isoformat(), "status_schema": 1}

        # Storm state + hold
        try:
            mon = WeatherMonitor(None)  # creds not needed for state
            st = mon._load_state()  # internal read
            last = st.get("last_alert_utc")
            payload["last_alert_utc"] = last
            hold = {"active": False, "remaining_seconds": None}
            if last:
                try:
                    ts = str(last)
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta = datetime.now(timezone.utc) - dt
                    active = delta < mon.storm_hold_time
                    hold["active"] = active
                    if active:
                        rem = (mon.storm_hold_time - delta).total_seconds()
                        hold["remaining_seconds"] = int(rem)
                except Exception:
                    pass
            payload["hold"] = hold
        except Exception:
            pass

        # Breaker
        try:
            open_now, until = is_breaker_open("ads")
            payload["breaker"] = {"open": bool(open_now), "until": until}
        except Exception:
            payload["breaker"] = {"open": None, "until": None}

        # Queue
        try:
            qs = get_queue_stats()
            items = list_queued_mutations(limit=10)
            payload["queue"] = {**qs, "items": items}
        except Exception:
            payload["queue"] = None

        # Area cooldowns
        try:
            payload["area_cooldowns"] = list_area_cooldowns(limit=10)
        except Exception:
            payload["area_cooldowns"] = None

        # Errors and top codes
        try:
            payload["recent_errors"] = list_recent_errors(limit=10)
        except Exception:
            payload["recent_errors"] = None
        try:
            payload["top_error_codes"] = summarize_error_codes(days=7, limit=5)
        except Exception:
            payload["top_error_codes"] = None

        # Campaign (best-effort, non-interactive)
        try:
            from .config import settings as cfg

            # Only try if token.json exists; don't open browser here
            creds = None
            if os.path.exists(TOKEN_FILE):
                try:
                    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                    from google.auth.transport.requests import Request as _GARequest

                    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                        creds.refresh(_GARequest())
                except Exception:
                    creds = None
            camp = {"ok": False, "id": cfg.CAMPAIGN_ID, "name": None, "status": None, "channel": None}
            if creds and cfg.DEVELOPER_TOKEN and cfg.CUSTOMER_ID and cfg.CAMPAIGN_ID:
                eff_version = get_config_value("api_version_override") or cfg.API_VERSION
                url = f"https://googleads.googleapis.com/{eff_version}/customers/{cfg.CUSTOMER_ID}/googleAds:search"
                headers = {
                    "Authorization": f"Bearer {creds.token}",
                    "Content-Type": "application/json",
                    "developer-token": cfg.DEVELOPER_TOKEN,
                }
                if cfg.LOGIN_CUSTOMER_ID:
                    headers["login-customer-id"] = cfg.LOGIN_CUSTOMER_ID
                q = (
                    "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type "
                    f"FROM campaign WHERE campaign.id = {cfg.CAMPAIGN_ID} LIMIT 1"  # nosec B608: GAQL with numeric ID only; no user input
                )
                r = requests.post(
                    url,
                    headers=headers,
                    json={"query": q},
                    timeout=ADS_HTTP_TIMEOUT_SECONDS,
                )
                if r.ok:
                    rows = (r.json() or {}).get("results", [])
                    if rows:
                        c = rows[0].get("campaign", {})
                        camp.update(
                            {
                                "ok": True,
                                "name": c.get("name"),
                                "status": c.get("status"),
                                "channel": c.get("advertisingChannelType"),
                            }
                        )
            payload["campaign"] = camp
        except Exception:
            payload["campaign"] = None

        print(_json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logging.error("Status-json failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "status-json"})
        except Exception:
            pass


def _cmd_status(args) -> None:
    try:
        from .config import settings as cfg
        from .db import (
            ensure_schema,
            get_config_value,
            get_queue_stats,
            is_breaker_open,
            list_area_cooldowns,
            list_queued_mutations,
            list_recent_errors,
            summarize_error_codes,
        )
        from .lsa_client import LSAClient
        from .weather_monitor import WeatherMonitor

        creds = get_credentials()
        ensure_schema()
        # Ensure fresh access token
        try:
            from google.auth.transport.requests import Request as _GARequest

            if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                creds.refresh(_GARequest())
        except Exception:
            pass
        # Campaign info
        # Respect runtime override if present
        eff_version = get_config_value("api_version_override") or cfg.API_VERSION
        url = f"https://googleads.googleapis.com/{eff_version}/customers/{cfg.CUSTOMER_ID}/googleAds:search"
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "developer-token": cfg.DEVELOPER_TOKEN,
        }
        if cfg.LOGIN_CUSTOMER_ID:
            headers["login-customer-id"] = cfg.LOGIN_CUSTOMER_ID
        q = (
            "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type "
            f"FROM campaign WHERE campaign.id = {cfg.CAMPAIGN_ID} LIMIT 1"  # nosec B608: GAQL with numeric ID only; no user input
        )
        resp = requests.post(
            url,
            headers=headers,
            json={"query": q},
            timeout=ADS_HTTP_TIMEOUT_SECONDS,
        )
        if resp.ok:
            rows = (resp.json() or {}).get("results", [])
            if rows:
                c = rows[0].get("campaign", {})
                logging.info(
                    "Campaign: %s (ID=%s) status=%s channel=%s",
                    c.get("name"),
                    c.get("id"),
                    c.get("status"),
                    c.get("advertisingChannelType"),
                )
            else:
                logging.warning("Campaign ID not found.")
        else:
            logging.error("Failed to fetch campaign status: HTTP %s", resp.status_code)
        # Storm hold info
        mon = WeatherMonitor(creds)
        state = mon._load_state()  # internal read; safe
        last = state.get("last_alert_utc")
        logging.info("Last alert (UTC): %s", last or "None")
        # Storm hold details
        try:
            from datetime import datetime, timezone

            hold_active = False
            remaining = None
            if last:
                last_dt = None
                try:
                    # Normalize Z to +00:00
                    _ts = str(last)
                    if _ts.endswith("Z"):
                        _ts = _ts[:-1] + "+00:00"
                    last_dt = datetime.fromisoformat(_ts)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    last_dt = None
                if last_dt is not None:
                    now_utc = datetime.now(timezone.utc)
                    delta = now_utc - last_dt
                    hold_active = delta < mon.storm_hold_time
                    if hold_active:
                        remaining = mon.storm_hold_time - delta
            if hold_active:
                logging.info("Storm hold: ACTIVE (remaining ~%s)", remaining)
            else:
                logging.info("Storm hold: INACTIVE")
        except Exception:
            pass
        # Circuit breaker info
        try:
            open_now, until = is_breaker_open("ads")
            if open_now:
                logging.warning("Ads circuit breaker: OPEN until %s", until or "unknown")
            else:
                logging.info("Ads circuit breaker: CLOSED")
        except Exception:
            pass
        # Queue stats
        try:
            qs = get_queue_stats()
            logging.info(
                "Queue: queued=%d running=%d done=%d error=%d",
                qs.get("queued", 0),
                qs.get("running", 0),
                qs.get("done", 0),
                qs.get("error", 0),
            )
            # Show a few queued items
            queued = list_queued_mutations(limit=10)
            if queued:
                logging.info("Queued mutations (up to 10):")
                for r in queued:
                    logging.info(
                        "- id=%s at=%s %s %s/%s attempts=%d err=%s",
                        r.get("id"),
                        r.get("created_at"),
                        r.get("action"),
                        r.get("customer_id"),
                        r.get("campaign_id"),
                        r.get("attempt_count", 0),
                        (r.get("last_error") or "")[:120],
                    )
        except Exception:
            pass
        # Area cooldowns (recent)
        try:
            cds = list_area_cooldowns(limit=10)
            if cds:
                logging.info("Recent area cooldowns (up to 10):")
                for c in cds:
                    logging.info(
                        "- area=%s last_action=%s at=%s",
                        c.get("area_id"),
                        c.get("last_action"),
                        c.get("changed_at"),
                    )
        except Exception:
            pass
        # Recent errors
        try:
            errs = list_recent_errors(limit=10)
            if errs:
                logging.info("Recent errors (up to 10):")
                for e in errs:
                    logging.info(
                        "- [%s] at=%s %s | %s",
                        e.get("source"),
                        e.get("created_at"),
                        (e.get("message") or "")[:200],
                        e.get("context"),
                    )
            # Top error codes summary (last 7 days)
            top = summarize_error_codes(days=7, limit=5)
            if top:
                logging.info("Top error codes/messages (7d):")
                for t in top:
                    logging.info("- %s: %d", t.get("key"), int(t.get("count", 0)))
        except Exception:
            pass
    except Exception as e:
        logging.error("Status failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "status"})
        except Exception:
            pass


def _cmd_scheduler(args) -> None:
    if not getattr(args, "scheduler", False):
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        logging.error("APScheduler not installed. Add apscheduler to requirements.txt. (%s)", e)
        return
    try:
        from datetime import datetime, timedelta, timezone

        from .lsa_reporting import LSAReportingClient
        from .weather_monitor import WeatherMonitor
        from .worker import drain_queue

        creds = get_credentials()
        mon = WeatherMonitor(creds)
        sched = BackgroundScheduler()
        # Schedule weather checks every 5 minutes
        sched.add_job(
            lambda: mon.update_campaign_status(),
            "interval",
            minutes=5,
            id="weather_check",
            max_instances=1,
            coalesce=True,
        )
        # Schedule queue drain every minute
        sched.add_job(
            lambda: drain_queue(creds), "interval", minutes=1, id="queue_drain", max_instances=1, coalesce=True
        )
        # Optional nightly LSA report exports (yesterday window)
        try:
            from .config import settings as cfg

            def _normalize_login(login: str | None) -> str | None:
                if not login:
                    return None
                try:
                    return str(int(str(login).replace("-", "").strip()))
                except Exception:
                    return str(login)

            def _run_lsa_nightly():
                try:
                    enabled = os.getenv("LSA_NIGHTLY_EXPORTS_ENABLED", "true").lower() in {"1", "true", "yes"}
                    if not enabled:
                        return
                    manager = _normalize_login(getattr(cfg, "LOGIN_CUSTOMER_ID", None))
                    if not manager:
                        logging.debug("LSA nightly export skipped: missing LOGIN_CUSTOMER_ID")
                        return
                    account = getattr(cfg, "LSA_ACCOUNT", "") or None
                    # Use previous calendar day (local time)
                    today = datetime.now().date()
                    day = today - timedelta(days=1)
                    start_d = day
                    end_d = day
                    leads = LSAReportingClient(creds).search_detailed_leads(
                        start=start_d, end=end_d, account_id=account, manager_customer_id=manager, page_size=1000
                    )
                    accounts = LSAReportingClient(creds).search_account_reports(
                        start=start_d, end=end_d, account_id=account, manager_customer_id=manager, page_size=1000
                    )
                    # Write to logs/lsa_auto/YYYY-MM-DD/
                    out_dir = os.path.join(LOG_DIR, "lsa_auto", str(day))
                    os.makedirs(out_dir, exist_ok=True)
                    import json as _json

                    with open(os.path.join(out_dir, "leads.json"), "w", encoding="utf-8") as f:
                        f.write(_json.dumps([lead.__dict__ for lead in leads], ensure_ascii=False, indent=2))
                    with open(os.path.join(out_dir, "accounts.json"), "w", encoding="utf-8") as f:
                        f.write(_json.dumps(accounts, ensure_ascii=False, indent=2))
                    logging.info(
                        "LSA nightly export wrote %d leads and %d account rows to %s",
                        len(leads),
                        len(accounts),
                        out_dir,
                    )
                    # Simple retention cleanup
                    try:
                        keep_days = int(os.getenv("LSA_EXPORT_RETENTION_DAYS", "30"))
                    except Exception:
                        keep_days = 30
                    cutoff = datetime.now() - timedelta(days=keep_days)
                    base = os.path.join(LOG_DIR, "lsa_auto")
                    if os.path.isdir(base):
                        for name in os.listdir(base):
                            path = os.path.join(base, name)
                            if os.path.isdir(path):
                                # name format YYYY-MM-DD
                                try:
                                    dt = datetime.strptime(name, "%Y-%m-%d")
                                    if dt < cutoff:
                                        import shutil

                                        shutil.rmtree(path, ignore_errors=True)
                                except Exception:
                                    pass
                except Exception as e:
                    logging.error("LSA nightly export failed: %s", e)
                    try:
                        capture_error(e, tags={"job": "lsa-nightly-export"})
                    except Exception:
                        pass

            # Schedule at configured local hour (default 02:15)
            try:
                hour = int(os.getenv("LSA_NIGHTLY_EXPORT_HOUR", "2"))
                minute = int(os.getenv("LSA_NIGHTLY_EXPORT_MINUTE", "15"))
            except Exception:
                hour, minute = 2, 15
            sched.add_job(
                _run_lsa_nightly,
                "cron",
                hour=hour,
                minute=minute,
                id="lsa_nightly_export",
                max_instances=1,
                coalesce=True,
            )
            logging.info("Nightly LSA export scheduled at %02d:%02d local time", hour, minute)
        except Exception:
            pass
        # Optionally start health and metrics servers
        try:
            from .config import settings as cfg

            if getattr(cfg, "METRICS_PORT", 0):
                start_metrics_server(cfg.METRICS_PORT)
                logging.info("Metrics exporter listening on :%d/metrics", cfg.METRICS_PORT)
            if getattr(cfg, "HEALTH_PORT", 0):
                from .health import start_health_server

                start_health_server(cfg.HEALTH_PORT)
        except Exception:
            pass
        sched.start()
        logging.info("Scheduler started (weather:5m, queue:1m). Press Ctrl+C to exit.")
        import time as _t

        try:
            while True:
                _t.sleep(60)
        except KeyboardInterrupt:
            logging.info("Shutting down scheduler...")
            sched.shutdown()
    except Exception as e:
        logging.error("Scheduler failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "scheduler"})
        except Exception:
            pass


def _cmd_doctor_live(args) -> None:
    # Extended live checks: SMTP login, Ads token refresh, DB write, and clock skew
    from .config import settings as cfg

    problems = []
    warnings = []
    # Dry-run guard
    if os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}:
        warnings.append("DRY_RUN is enabled; disable it for live runs")
    # Ads config
    if not (cfg.DEVELOPER_TOKEN or get_secret("developer_token")):
        problems.append("Missing developer token")
    if not cfg.CUSTOMER_ID or not cfg.CAMPAIGN_ID:
        problems.append("Missing CUSTOMER_ID or CAMPAIGN_ID")
    # SMTP login (optional)
    if getattr(cfg, "ENABLE_NOTIFICATIONS", False) and getattr(cfg, "ENABLE_EMAIL", False):
        import smtplib

        try:
            server = smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=5)
            try:
                server.starttls()
            except Exception:
                pass
            if cfg.SMTP_USER:
                pw = cfg.SMTP_PASSWORD or get_secret("smtp_password")
                if pw:
                    server.login(cfg.SMTP_USER, pw)
            server.quit()
        except Exception as e:
            warnings.append(f"SMTP check failed: {e}")
    # Ads token refresh (no interactive OAuth here)
    creds = None
    try:
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            from google.auth.transport.requests import Request as _GARequest

            if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                creds.refresh(_GARequest())
        else:
            warnings.append("No token.json found; run authentication once (python -m src) to generate it.")
    except Exception as e:
        warnings.append(f"Ads token refresh failed: {e}")
    # DB write test
    try:
        from .db import ensure_schema, get_conn

        ensure_schema()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS _doctor (t TEXT)")
            cur.execute("INSERT INTO _doctor (t) VALUES (datetime('now'))")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        problems.append(f"DB write failed: {e}")
    # Clock skew check via HTTP Date header
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        r = requests.get("https://www.google.com/generate_204", timeout=5, headers={"Cache-Control": "no-cache"})
        srv_date = r.headers.get("Date")
        if srv_date:
            dt = parsedate_to_datetime(srv_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            skew_s = abs((now - dt).total_seconds())
            if skew_s > 120:
                warnings.append(
                    f"System clock skew ~{int(skew_s)}s vs server; correct system time to avoid auth issues."
                )
        elif getattr(creds, "expired", False) and not getattr(creds, "refresh_token", None):
            warnings.append("Token appears expired and not refreshable; check system clock and re-authenticate.")
    except Exception:
        pass
    # Report
    if problems:
        logging.error("Doctor-live: FAIL")
        for p in problems:
            logging.error("- %s", p)
    else:
        logging.info("Doctor-live: OK (no blocking issues)")
    for w in warnings:
        logging.warning("- %s", w)


def _cmd_show_version(args) -> None:
    try:
        from .config import settings as cfg
        from .db import ensure_schema, get_config_value

        ensure_schema()
        eff = get_config_value("api_version_override") or cfg.API_VERSION
        can = cfg.API_VERSION_CANARY or "(unset)"
        logging.info("API version: effective=%s default=%s canary=%s", eff, cfg.API_VERSION, can)
    except Exception as e:
        logging.error("Show-version failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "show-version"})
        except Exception:
            pass


def _cmd_promote_canary(args) -> None:
    try:
        from .config import settings as cfg
        from .db import ensure_schema, get_config_value, record_audit_log, set_config_value

        ensure_schema()
        if not cfg.API_VERSION_CANARY:
            logging.error("No API_VERSION_CANARY configured. Set GOOGLE_ADS_API_VERSION_CANARY first.")
            return
        old_eff = get_config_value("api_version_override") or cfg.API_VERSION
        set_config_value("api_version_override", cfg.API_VERSION_CANARY)
        logging.info("Promoted API version to canary: %s", cfg.API_VERSION_CANARY)
        try:
            record_audit_log(
                who="system",
                what="ads.api_version",
                why="promote_canary",
                old_value=old_eff,
                new_value=cfg.API_VERSION_CANARY,
                outcome="ok",
                error=None,
                extras=None,
            )
        except Exception:
            pass
    except Exception as e:
        logging.error("Promote-canary failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "promote-canary"})
        except Exception:
            pass


def _cmd_demote_canary(args) -> None:
    try:
        from .config import settings as cfg
        from .db import delete_config_value, ensure_schema, get_config_value, record_audit_log

        ensure_schema()
        old_eff = get_config_value("api_version_override") or cfg.API_VERSION
        delete_config_value("api_version_override")
        logging.info("Cleared API version override; default will be used.")
        try:
            record_audit_log(
                who="system",
                what="ads.api_version",
                why="demote_canary",
                old_value=old_eff,
                new_value=cfg.API_VERSION,
                outcome="ok",
                error=None,
                extras=None,
            )
        except Exception:
            pass
    except Exception as e:
        logging.error("Demote-canary failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "demote-canary"})
        except Exception:
            pass


def _cmd_promote_if_healthy(args) -> None:
    try:
        from .config import settings as cfg

        if not cfg.API_VERSION_CANARY:
            logging.error("No API_VERSION_CANARY configured. Set GOOGLE_ADS_API_VERSION_CANARY first.")
            return
        from .db import ensure_schema, get_config_value, record_audit_log, set_config_value
        from .lsa_client import LSAClient

        ensure_schema()
        creds = get_credentials()
        diag = LSAClient(creds).diagnose_ads_canary()
        if diag.get("ok"):
            old_eff = get_config_value("api_version_override") or cfg.API_VERSION
            set_config_value("api_version_override", cfg.API_VERSION_CANARY)
            logging.info("Promoted API version to canary (healthy): %s", cfg.API_VERSION_CANARY)
            try:
                record_audit_log(
                    who="system",
                    what="ads.api_version",
                    why="promote_if_healthy",
                    old_value=old_eff,
                    new_value=cfg.API_VERSION_CANARY,
                    outcome="ok",
                    error=None,
                    extras=dict(diag),
                )
            except Exception:
                pass
        else:
            logging.error("Canary probe failed; not promoting: %s", diag.get("message"))
            hints = diag.get("hints") or []
            for h in hints:
                logging.warning("- %s", h)
    except Exception as e:
        logging.error("Promote-if-healthy failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "promote-if-healthy"})
        except Exception:
            pass


def _cmd_caps(args) -> None:
    try:
        from .db import ensure_schema, list_latest_caps

        ensure_schema()
        rows = list_latest_caps(limit=getattr(args, "caps_limit", 25))
        if not rows:
            logging.info("No CAP alerts found.")
        for r in rows:
            logging.info(
                "- %s effective=%s severity=%s areas=%s",
                r.get("cap_id"),
                r.get("effective_at"),
                r.get("severity"),
                r.get("areas") or "",
            )
    except Exception as e:
        logging.error("CAP list failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "caps"})
        except Exception:
            pass


def _cmd_rules_dry_run(args) -> None:
    try:
        from .config import settings as cfg
        from .db import ensure_schema, list_latest_caps
        from .rules import evaluate as _eval_rules
        from .rules import load_rules as _load_rules

        ensure_schema()
        rules_path = getattr(args, "rules_file", None) or getattr(cfg, "RULES_FILE", None)
        if not rules_path:
            logging.error("No rules file configured. Use --rules-file or set RULES_FILE in settings.")
            return
        try:
            rules = _load_rules(rules_path)
        except Exception as e:
            logging.error("Failed to load rules from %s: %s", rules_path, e)
            return
        rows = list_latest_caps(limit=getattr(args, "rules_dry_run_limit", 25))
        if not rows:
            logging.info("No CAP alerts found.")
            return
        from datetime import datetime, timezone

        def _parse_iso(ts: str):
            try:
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                return datetime.fromisoformat(ts)
            except Exception:
                return None

        def _split_counties(areas_val) -> list[str]:
            result: list[str] = []
            for a in str(areas_val).split(","):
                if not a:
                    continue
                s = str(a).strip()
                if s:
                    result.append(s)
            return result

        for r in rows:
            eff = r.get("effective_at")
            sev = r.get("severity")
            areas = r.get("areas") or ""
            counties = _split_counties(areas)
            age_min = None
            if eff:
                dt = _parse_iso(str(eff))
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    try:
                        age_min = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
                    except Exception:
                        age_min = None
            try:
                decision = _eval_rules(
                    rules, severity=sev, event=None, counties_fips=counties, alert_age_minutes=age_min
                )
            except Exception as e:
                decision = None
                logging.warning("Rule evaluation error for CAP %s: %s", r.get("cap_id"), e)
            logging.info(
                "- cap=%s effective=%s severity=%s areas=%s decision=%s",
                r.get("cap_id"),
                eff,
                sev,
                areas,
                decision or "None",
            )
    except Exception as e:
        logging.error("Rules dry-run failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "rules-dry-run"})
        except Exception:
            pass


def _cmd_export_audit(args) -> None:
    try:
        from .db import ensure_schema, export_audit

        ensure_schema()
        path = export_audit(days=getattr(args, "days", 7), fmt=getattr(args, "format", "jsonl"))
        logging.info("Export completed: %s", path)
    except Exception as e:
        logging.error("Export failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "export-audit"})
        except Exception:
            pass


def _cmd_purge_placeholder_queue(args) -> None:
    try:
        import sqlite3

        from .db import ensure_queue_schema

        ensure_queue_schema()
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db")
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(
            "DELETE FROM mutation_queue WHERE (customer_id IN ('9999999999','') OR campaign_id IN ('2222222222','')) AND status='queued'"
        )
        n = cur.rowcount
        con.commit()
        print(f"Purged {n} placeholder queued mutations")
    except Exception as e:
        print(f"Purge failed: {e}")


def _cmd_queue_status(args) -> None:
    from .db import ensure_schema, get_queue_stats

    ensure_schema()
    stats = get_queue_stats()
    logging.info(
        "Queue: queued=%d running=%d done=%d error=%d",
        stats.get("queued", 0),
        stats.get("running", 0),
        stats.get("done", 0),
        stats.get("error", 0),
    )


def _cmd_drain_queue(args) -> None:
    from .worker import drain_queue as _drain

    credentials = get_credentials()
    count = _drain(credentials)
    logging.info("Queue drained via worker. processed=%d", count)


def _cmd_lsa_export(args) -> None:
    try:
        import os as _os
        from datetime import date, datetime

        from .config import settings as cfg
        from .lsa_reporting import LSAReportingClient

        # Parse dates
        def _parse_date(s: str | None) -> date | None:
            if not s:
                return None
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except Exception:
                return None

        start_s = getattr(args, "start", None)
        end_s = getattr(args, "end", None)
        start_d = _parse_date(start_s)
        end_d = _parse_date(end_s)
        if not start_d or not end_d:
            logging.error("--start and --end are required in YYYY-MM-DD format for LSA exports")
            return
        if end_d < start_d:
            logging.error("End date must be >= start date")
            return
        # Manager CID (login customer id) is required by the LSA reporting API
        manager = getattr(cfg, "LOGIN_CUSTOMER_ID", None)
        if not manager:
            logging.error("GOOGLE_ADS_LOGIN_CUSTOMER_ID is required for LSA reporting exports")
            return
        # Normalize dashes
        try:
            manager = str(int(str(manager).replace("-", "").strip()))
        except Exception:
            manager = str(manager)

        creds = get_credentials()
        client = LSAReportingClient(creds)
        lsa_account = getattr(cfg, "LSA_ACCOUNT", "") or None
        # Build default output path
        out = getattr(args, "output", None)
        fmt = getattr(args, "lsa_format", "json")
        if getattr(args, "lsa_export_leads", False):
            rows = client.search_detailed_leads(
                start=start_d, end=end_d, account_id=lsa_account, manager_customer_id=manager, page_size=1000
            )
            export_rows = [r.__dict__ for r in rows]
            if not out:
                out = f"logs/lsa_leads_{start_d}_{end_d}.{fmt}"
        else:
            rows = client.search_account_reports(
                start=start_d, end=end_d, account_id=lsa_account, manager_customer_id=manager, page_size=1000
            )
            export_rows = rows
            if not out:
                out = f"logs/lsa_accounts_{start_d}_{end_d}.{fmt}"

        dirn = _os.path.dirname(out) or "."
        _os.makedirs(dirn, exist_ok=True)
        if fmt == "json":
            import json as _json

            with open(out, "w", encoding="utf-8") as f:
                f.write(_json.dumps(export_rows, ensure_ascii=False, indent=2))
            logging.info("Exported %d rows to %s", len(export_rows), out)
        else:
            # CSV export
            def _to_csv(rows: list[dict]) -> str:
                if not rows:
                    return ""
                cols: list[str] = []
                for r in rows:
                    for k in r.keys():
                        if k not in cols:
                            cols.append(k)
                import io

                buf = io.StringIO()
                buf.write(",".join(cols) + "\n")
                for r in rows:
                    values = []
                    for c in cols:
                        v = r.get(c)
                        if v is None:
                            values.append("")
                        else:
                            s = str(v)
                            if any(ch in s for ch in [",", "\n", '"']):
                                s = '"' + s.replace('"', '""') + '"'
                            values.append(s)
                    buf.write(",".join(values) + "\n")
                return buf.getvalue()

            dict_rows = export_rows if isinstance(export_rows, list) else []
            with open(out, "w", encoding="utf-8") as f:
                f.write(_to_csv(dict_rows))
            logging.info("Exported %d rows to %s", len(dict_rows), out)
    except Exception as e:
        logging.error("LSA report export failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "lsa-export"})
        except Exception:
            pass


def _cmd_lsa_diagnose(args) -> None:
    try:
        from datetime import date, timedelta

        import requests as _rq

        from .config import settings as cfg
        from .config.settings import API_VERSION, DEVELOPER_TOKEN
        from .lsa_reporting import LSAReportingClient

        def _norm(s: str | None) -> str | None:
            if not s:
                return None
            try:
                return str(int(str(s).replace("-", "").strip()))
            except Exception:
                return str(s).replace("-", "").strip()

        creds = get_credentials()
        client = LSAReportingClient(creds)
        # Candidates: LOGIN_CUSTOMER_ID, CUSTOMER_ID
        candidates: list[str] = []
        for c in (_norm(getattr(cfg, "LOGIN_CUSTOMER_ID", None)), _norm(getattr(cfg, "CUSTOMER_ID", None))):
            if c and c not in candidates:
                candidates.append(c)
        # Also pull from Google Ads: listAccessibleCustomers (requires developer token)
        if not DEVELOPER_TOKEN:
            print("GOOGLE_ADS_DEVELOPER_TOKEN not set; skipping listAccessibleCustomers discovery.")
        else:
            try:
                ads_url = f"https://googleads.googleapis.com/{API_VERSION}/customers:listAccessibleCustomers"
                hdrs = {
                    "Authorization": f"Bearer {creds.token}",
                    "developer-token": DEVELOPER_TOKEN,
                }
                resp = _rq.get(ads_url, headers=hdrs, timeout=30)
                if resp.ok:
                    data = resp.json()
                    for rn in data.get("resourceNames", []) or []:
                        # rn like "customers/1234567890"
                        try:
                            cid = rn.split("/")[-1]
                            cidn = _norm(cid)
                            if cidn and cidn not in candidates:
                                candidates.append(cidn)
                        except Exception:
                            continue
                    if data.get("resourceNames"):
                        print(f"Discovered {len(data.get('resourceNames', []))} accessible customers via Ads API.")
                else:
                    print(f"listAccessibleCustomers failed: {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                print(f"Error calling listAccessibleCustomers: {e}")
        if not candidates:
            print("No candidate manager/customer IDs found. Set GOOGLE_ADS_LOGIN_CUSTOMER_ID in .env (digits only).")
            return
        acct = getattr(cfg, "LSA_ACCOUNT", "") or None
        acct_num = _norm(str(acct).replace("accounts/", "")) if acct else None
        s = date.today() - timedelta(days=7)
        e = date.today()
        best: str | None = None
        for cand in candidates:
            print(f"Testing manager_customer_id={cand}...")
            aggs = client.search_account_reports(
                start=s, end=e, account_id=None, manager_customer_id=cand, page_size=100
            )
            print(f"  accountReports rows: {len(aggs)}")
            # Collect a few discovered accountIds to help configure LSA_ACCOUNT
            if aggs:
                seen = []
                for r in aggs:
                    aid = str(r.get("accountId") or "").strip()
                    if aid and aid not in seen:
                        seen.append(aid)
                    if len(seen) >= 5:
                        break
                if seen:
                    print("  discovered accountIds:", ", ".join(seen))
                    print(f"  example LSA_ACCOUNT: accounts/{seen[0]}")
            if acct_num:
                leads = client.search_detailed_leads(
                    start=s, end=e, account_id=acct_num, manager_customer_id=cand, page_size=100
                )
                print(f"  detailedLeadReports rows (acct {acct_num}): {len(leads)}")
            if not best and aggs:
                best = cand
        if best:
            print(f"Suggested GOOGLE_ADS_LOGIN_CUSTOMER_ID: {best}")
        else:
            print(
                "No aggregates found with tested IDs. Ensure your login is an MCC/manager that links the LSA account."
            )
    except Exception as e:
        logging.error("LSA diagnose failed: %s", e)


def _cmd_lsa_ids(args) -> None:
    try:
        import json as _json
        from datetime import date, timedelta

        import requests as _rq

        from .config.settings import API_VERSION, DEVELOPER_TOKEN
        from .lsa_reporting import LSAReportingClient

        def _norm(s: str | None) -> str | None:
            if not s:
                return None
            try:
                return str(int(str(s).replace("-", "").strip()))
            except Exception:
                return str(s).replace("-", "").strip()

        creds = get_credentials()
        out = {"accessible_customers": [], "lsa_accounts_by_manager": {}}
        # Step 1: list accessible Ads customers
        if not DEVELOPER_TOKEN:
            print("GOOGLE_ADS_DEVELOPER_TOKEN not set; cannot call listAccessibleCustomers.")
        else:
            ads_url = f"https://googleads.googleapis.com/{API_VERSION}/customers:listAccessibleCustomers"
            hdrs = {"Authorization": f"Bearer {creds.token}", "developer-token": DEVELOPER_TOKEN}
            resp = _rq.get(ads_url, headers=hdrs, timeout=30)
            if resp.ok:
                data = resp.json() or {}
                for rn in data.get("resourceNames", []) or []:
                    cid = rn.split("/")[-1]
                    out["accessible_customers"].append(_norm(cid))
            else:
                print(f"listAccessibleCustomers failed: {resp.status_code} {resp.text[:200]}")

        # Step 2: for each candidate manager, discover LSA accountIds via Aggregates
        s = date.today() - timedelta(days=14)
        e = date.today()
        lsa = LSAReportingClient(creds)
        managers = [c for c in out["accessible_customers"] if c]
        if not managers:
            print("No accessible customers discovered; ensure OAuth and developer token are configured.")
        for mgr in managers[:10]:  # limit to first 10 to be quick
            rows = lsa.search_account_reports(start=s, end=e, account_id=None, manager_customer_id=mgr, page_size=200)
            ids = []
            for r in rows:
                aid = str(r.get("accountId") or "").strip()
                if aid and aid not in ids:
                    ids.append(aid)
            if ids:
                out["lsa_accounts_by_manager"][mgr] = ids

        # Print summary
        print("Accessible Ads customers (CIDs):", ", ".join(out["accessible_customers"]) or "<none>")
        if out["lsa_accounts_by_manager"]:
            print("Discovered LSA accountIds by manager:")
            for mgr, ids in out["lsa_accounts_by_manager"].items():
                print(f"  {mgr}: {', '.join(ids)}")
                print(f"    example LSA_ACCOUNT: accounts/{ids[0]}")
        else:
            print("No LSA accountIds discovered. Verify the correct manager CID and that accounts are linked.")

        # Optional JSON output
        if getattr(args, "output", None):
            try:
                import os as _os

                dirn = _os.path.dirname(args.output) or "."
                _os.makedirs(dirn, exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(_json.dumps(out, indent=2))
                print(f"Wrote {args.output}")
            except Exception as ie:
                print(f"Failed to write output: {ie}")
    except Exception as e:
        logging.error("LSA IDs discovery failed: %s", e)


def _cmd_lsa_configure(args) -> None:
    try:
        from datetime import date, timedelta

        import requests as _rq

        from .config.settings import API_VERSION, DEVELOPER_TOKEN
        from .lsa_reporting import LSAReportingClient

        def _norm(s: str | None) -> str | None:
            if not s:
                return None
            try:
                return str(int(str(s).replace("-", "").strip()))
            except Exception:
                return str(s).replace("-", "").strip()

        creds = get_credentials()
        # Step 1: discover accessible Ads customers
        managers: list[str] = []
        if not DEVELOPER_TOKEN:
            print("GOOGLE_ADS_DEVELOPER_TOKEN not set; cannot auto-configure.")
            return
        ads_url = f"https://googleads.googleapis.com/{API_VERSION}/customers:listAccessibleCustomers"
        hdrs = {"Authorization": f"Bearer {creds.token}", "developer-token": DEVELOPER_TOKEN}
        resp = _rq.get(ads_url, headers=hdrs, timeout=30)
        if resp.ok:
            data = resp.json() or {}
            for rn in data.get("resourceNames", []) or []:
                cid = rn.split("/")[-1]
                cidn = _norm(cid)
                if cidn and cidn not in managers:
                    managers.append(cidn)
        else:
            print(f"listAccessibleCustomers failed: {resp.status_code} {resp.text[:200]}")
            return
        if not managers:
            print("No accessible Ads customers found.")
            return
        # Step 2: find first manager with LSA accounts
        lsa = LSAReportingClient(creds)
        s = date.today() - timedelta(days=14)
        e = date.today()
        chosen_mgr: str | None = None
        chosen_acct: str | None = None
        for mgr in managers:
            rows = lsa.search_account_reports(start=s, end=e, account_id=None, manager_customer_id=mgr, page_size=200)
            ids = []
            for r in rows:
                aid = str(r.get("accountId") or "").strip()
                if aid and aid not in ids:
                    ids.append(aid)
            if ids:
                chosen_mgr = mgr
                chosen_acct = ids[0]
                break
        if not chosen_mgr:
            print("Could not find any LSA accounts under accessible managers.")
            return
        # Step 3: write to .env
        import os as _os

        env_path = _os.path.join(PROJECT_ROOT, ".env")
        lines: list[str] = []
        if _os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()

        def upsert(k: str, v: str):
            pref = f"{k}="
            found = False
            for i, line in enumerate(lines):
                if line.strip().startswith(pref):
                    lines[i] = f"{k}={v}"
                    found = True
                    break
            if not found:
                lines.append(f"{k}={v}")

        upsert("GOOGLE_ADS_LOGIN_CUSTOMER_ID", chosen_mgr)
        if chosen_acct:
            upsert("LSA_ACCOUNT", f"accounts/{chosen_acct}")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Configured .env with GOOGLE_ADS_LOGIN_CUSTOMER_ID={chosen_mgr} and LSA_ACCOUNT=accounts/{chosen_acct}")
    except Exception as e:
        logging.error("LSA auto-configure failed: %s", e)


def _cmd_map_region(args) -> None:
    try:
        from .db import ensure_schema, upsert_region_mapping

        ensure_schema()
        area_id, camp_id = args.map_region
        upsert_region_mapping(area_id, camp_id, args.map_customer)
        logging.info("Mapped area %s -> campaign %s (customer=%s)", area_id, camp_id, args.map_customer or "")
    except Exception as e:
        logging.error("Map region failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "map-region"})
        except Exception:
            pass


def _cmd_unmap_region(args) -> None:
    try:
        from .db import delete_region_mapping, ensure_schema

        ensure_schema()
        delete_region_mapping(args.unmap_region)
        logging.info("Unmapped area %s", args.unmap_region)
    except Exception as e:
        logging.error("Unmap region failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "unmap-region"})
        except Exception:
            pass


def _cmd_list_regions(args) -> None:
    try:
        from .db import ensure_schema, list_region_mappings

        ensure_schema()
        rows = list_region_mappings()
        if not rows:
            logging.info("No region mappings.")
        for r in rows:
            logging.info("- %s -> %s (customer=%s)", r.get("area_id"), r.get("campaign_id"), r.get("customer_id") or "")
    except Exception as e:
        logging.error("List regions failed: %s", e)
        try:
            capture_error(e, tags={"cmd": "list-regions"})
        except Exception:
            pass


def _cmd_doctor_ads(args) -> None:
    try:
        from .lsa_client import LSAClient

        creds = get_credentials()
        diag = LSAClient(creds).diagnose_ads_access()
        if diag.get("ok"):
            logging.info("Doctor-ads: OK - %s", diag.get("message"))
        else:
            logging.error("Doctor-ads: FAIL - %s", diag.get("message"))
            sc = diag.get("status_code")
            if sc:
                logging.error("HTTP status: %s", sc)
            hints = diag.get("hints") or []
            for h in hints:
                logging.warning("- %s", h)
    except Exception as e:
        logging.error("Doctor-ads encountered an error: %s", e)
        try:
            capture_error(e, tags={"cmd": "doctor-ads"})
        except Exception:
            pass


def _cmd_doctor_ads_canary(args) -> None:
    try:
        from .lsa_client import LSAClient

        creds = get_credentials()
        diag = LSAClient(creds).diagnose_ads_canary()
        if diag.get("ok"):
            logging.info("Doctor-ads-canary: OK - %s", diag.get("message"))
        else:
            logging.error("Doctor-ads-canary: FAIL - %s", diag.get("message"))
            sc = diag.get("status_code")
            if sc:
                logging.error("HTTP status: %s", sc)
            hints = diag.get("hints") or []
            for h in hints:
                logging.warning("- %s", h)
    except Exception as e:
        logging.error("Doctor-ads-canary encountered an error: %s", e)
        try:
            capture_error(e, tags={"cmd": "doctor-ads-canary"})
        except Exception:
            pass


def main():
    args = parse_args()
    # Apply CLI -> env overrides before importing modules that read settings
    apply_env_overrides(args)

    # Initialize Sentry and OpenTelemetry if configured
    try:
        from .config import settings as cfg

        # Migrate secrets into keyring (best-effort)
        try:
            mig = migrate_from_files_and_env(TOKEN_FILE, cfg.DEVELOPER_TOKEN, cfg.SMTP_PASSWORD)
            if mig.get("developer_token"):
                logging.info("Developer token stored in keyring (%s)", masked(cfg.DEVELOPER_TOKEN))
            if mig.get("smtp_password"):
                logging.info("SMTP password stored in keyring")
            if mig.get("google_oauth_token"):
                logging.info("OAuth token migrated to keyring")
        except Exception:
            pass
        init_sentry(
            os.getenv("SENTRY_DSN"),
            os.getenv("SENTRY_ENV") or os.getenv("ENVIRONMENT"),
            float(os.getenv("SENTRY_TRACES", "0")),
        )
        init_otel()
    except Exception:
        pass

    # Adjust log level if provided
    if getattr(args, "log_level", None):
        level = getattr(logging, str(args.log_level).upper(), logging.INFO)
        logging.getLogger().setLevel(level)

    # Optional JSON logs
    if getattr(args, "json_logs", False):

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                import json as _json

                payload = {
                    "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
                return _json.dumps(payload, ensure_ascii=False)

        jf = JsonFormatter()
        for h in logging.getLogger().handlers:
            h.setFormatter(jf)

    if getattr(args, "init", False):
        # Ensure DB schema as part of init
        try:
            from .db import ensure_schema

            ensure_schema()
        except Exception as e:
            logging.warning("DB init skipped: %s", e)
        perform_init()
        return
    if getattr(args, "purge_placeholder_queue", False):
        _cmd_purge_placeholder_queue(args)
        return
    if getattr(args, "doctor", False):
        try:
            from .db import ensure_schema

            ensure_schema()
        except Exception:
            pass
        perform_doctor()
        return
    if getattr(args, "status_json", False):
        _cmd_status_json(args)
        return
    if getattr(args, "status", False):
        _cmd_status(args)
        return
    if getattr(args, "show_version", False):
        _cmd_show_version(args)
        return
    if getattr(args, "promote_canary", False):
        _cmd_promote_canary(args)
        return
    if getattr(args, "demote_canary", False):
        _cmd_demote_canary(args)
        return
    if getattr(args, "promote_if_healthy", False):
        _cmd_promote_if_healthy(args)
        return
    if getattr(args, "caps", False):
        _cmd_caps(args)
        return
    if getattr(args, "rules_dry_run", False):
        _cmd_rules_dry_run(args)
        return
    if getattr(args, "export_audit", False):
        _cmd_export_audit(args)
        return
    # LSA reporting exports (read-only)
    if getattr(args, "lsa_export_leads", False) or getattr(args, "lsa_export_accounts", False):
        _cmd_lsa_export(args)
        return
    # LSA diagnosis (find a working manager CID and verify detailed leads)
    if getattr(args, "lsa_diagnose", False):
        _cmd_lsa_diagnose(args)
        return
    # LSA IDs discovery (accessible Ads CIDs and LSA accountIds)
    if getattr(args, "lsa_ids", False):
        _cmd_lsa_ids(args)
        return
    # LSA auto-configure: discover a working MCC and LSA accountId and persist to .env
    if getattr(args, "lsa_configure", False):
        _cmd_lsa_configure(args)
        return
    if getattr(args, "map_region", None):
        _cmd_map_region(args)
        return
    if getattr(args, "unmap_region", None):
        _cmd_unmap_region(args)
        return
    if getattr(args, "list_regions", False):
        _cmd_list_regions(args)
        return
    if getattr(args, "doctor_ads", False):
        _cmd_doctor_ads(args)
        return
    if getattr(args, "doctor_ads_canary", False):
        _cmd_doctor_ads_canary(args)
        return
    if getattr(args, "notify_test", False):
        # Send a one-off test notification using configured channels
        try:
            from .notifier import Notifier

            Notifier().notify(subject="Weather-LSA Test", body="This is a test notification.")
            logging.info("Notify-test completed")
        except Exception as e:
            logging.error("Notify-test failed: %s", e)
            try:
                capture_error(e, tags={"cmd": "notify-test"})
            except Exception:
                pass
        return

    if getattr(args, "send_test_email", False):
        try:
            from .notifier import Notifier

            ok = Notifier().send_email(subject="Weather-LSA Test Email", body="This is a test email.")
            if ok:
                logging.info("Send-test-email: sent")
            else:
                logging.warning("Send-test-email: not sent (disabled or not configured)")
        except Exception as e:
            logging.error("Send-test-email failed: %s", e)
            try:
                capture_error(e, tags={"cmd": "send-test-email"})
            except Exception:
                pass
        return

    if getattr(args, "scheduler", False):
        _cmd_scheduler(args)
        return

    credentials = get_credentials()
    # Start metrics exporter and health endpoints if enabled
    try:
        from .config import settings as cfg

        if getattr(cfg, "METRICS_PORT", 0):
            start_metrics_server(cfg.METRICS_PORT)
            logging.info("Metrics exporter listening on :%d/metrics", cfg.METRICS_PORT)
        if getattr(cfg, "HEALTH_PORT", 0):
            from .health import start_health_server

            start_health_server(cfg.HEALTH_PORT)
    except Exception:
        pass

    # Import after env overrides so settings pick them up
    if os.getenv("LIST_CAMPAIGNS", "false").lower() in {"1", "true", "yes"}:
        from .lsa_client import LSAClient

        LSAClient(credentials).list_campaigns()
        return

    if os.getenv("CREATE_TEST_ACCOUNT", "false").lower() in {"1", "true", "yes"}:
        from .lsa_client import LSAClient

        LSAClient(credentials).create_test_account()
        return

    if getattr(args, "doctor_live", False):
        _cmd_doctor_live(args)
        return

    # Lightweight DB utilities
    from .db import ensure_schema

    ensure_schema()
    if getattr(args, "queue_status", False):
        _cmd_queue_status(args)
        return
    if getattr(args, "drain_queue", False):
        _cmd_drain_queue(args)
        return

    from .weather_monitor import WeatherMonitor

    monitor = WeatherMonitor(credentials)

    if getattr(args, "clear_hold", False):
        monitor.clear_hold()
        return

    monitor.update_campaign_status()


if __name__ == "__main__":
    main()
