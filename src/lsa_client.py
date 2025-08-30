import logging
import random
import time
from typing import Optional, TypedDict

import requests
from google.auth.transport.requests import Request

from .config.settings import (
    ADS_BACKOFF_BASE_SECONDS,
    ADS_BACKOFF_MAX_ATTEMPTS,
    ADS_BACKOFF_MAX_SLEEP,
    ADS_BREAKER_COOLDOWN_MIN,
    ADS_BREAKER_THRESHOLD,
    ADS_BURST,
    ADS_HTTP_TIMEOUT_SECONDS,
    ADS_QPS,
    API_ENDPOINT,
    CAMPAIGN_ID,
    CUSTOMER_ID,
    DEVELOPER_TOKEN,
    DRY_RUN,
    HAS_CUSTOMER_ID,
    LOGIN_CUSTOMER_ID,
    LSA_MUTATE_VIA_ADS_STATUS,
    REQUIRE_LOCAL_SERVICES_ONLY,
    REQUIRED_CAMPAIGN_LABELS,
    TEST_ACCOUNT_CURRENCY,
    TEST_ACCOUNT_NAME,
    TEST_ACCOUNT_TIME_ZONE,
    TEST_ACCOUNT_TRACKING_URL_TEMPLATE,
    VALIDATE_ONLY,
)
from .db import get_config_value, is_breaker_open, record_action, record_audit_log, record_breaker_result
from .metrics import inc_actions_applied, inc_ads_validate_ok, inc_api_errors, time_ads_mutate
from .observability import capture_error, sanitize
from .ratelimit import TokenBucket
from .tracing import start_span


def _post_with_timeout(url: str, headers=None, json=None):
    """POST with a timeout, but gracefully handle patched functions without it.

    Tests monkeypatch requests.post with stubs that don't accept the 'timeout' kwarg.
    This helper enforces timeouts in production but falls back for those stubs.
    """
    try:
        return requests.post(url, headers=headers, json=json, timeout=ADS_HTTP_TIMEOUT_SECONDS)
    except TypeError as e:
        if "timeout" in str(e):
            # Fallback path used only by unit tests that monkeypatch requests.post with a stub
            # that does not accept the 'timeout' kwarg.
            return requests.post(url, headers=headers, json=json)  # nosec B113: test-only fallback, not production
        raise


class _DiagResult(TypedDict, total=False):
    ok: bool
    status_code: Optional[int]
    error: Optional[str]
    message: Optional[str]
    hints: list[str]


class LSAClient:
    def __init__(self, credentials):
        self.credentials = credentials
        # Respect runtime override for API version if set in DB (best-effort)
        try:
            override = get_config_value("api_version_override")
        except Exception:
            override = None
        if override:
            self.base_url = f"https://googleads.googleapis.com/{override}"
        else:
            self.base_url = API_ENDPOINT
        # QPS limiter for Ads API calls
        self._bucket = TokenBucket(rate_per_sec=ADS_QPS, burst=ADS_BURST)

    @staticmethod
    def _sanitized_headers(headers: dict) -> dict:
        sanitized = dict(headers)
        if "Authorization" in sanitized:
            sanitized["Authorization"] = "Bearer ***"
        if "developer-token" in sanitized and sanitized["developer-token"]:
            token = sanitized["developer-token"]
            sanitized["developer-token"] = f"***{token[-4:]}" if len(token) > 4 else "***"
        return sanitized

    def list_campaigns(self, page_size: int = 50):
        try:
            # Circuit breaker: prevent calls when open
            open_now, until = is_breaker_open("ads")
            if open_now:
                logging.warning("Circuit breaker OPEN for Ads until %s; skipping list_campaigns.", until or "unknown")
                return []
            # Ensure valid access token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    logging.error(f"Failed to refresh OAuth token: {refresh_err}")
                    return []

            if not DEVELOPER_TOKEN:
                logging.error("Missing GOOGLE_ADS_DEVELOPER_TOKEN. Set it in your .env and restart.")
                return []
            if not HAS_CUSTOMER_ID:
                logging.error("Missing GOOGLE_ADS_CUSTOMER_ID in .env")
                return []

            url = f"{self.base_url}/customers/{CUSTOMER_ID}/googleAds:search"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID

            query = (
                "SELECT campaign.id, campaign.name, campaign.status, "
                f"campaign.advertising_channel_type FROM campaign LIMIT {page_size}"
            )
            payload = {"query": query}

            if DRY_RUN:
                logging.info(
                    "[DRY_RUN] Would POST %s with headers=%s payload=%s",
                    url,
                    self._sanitized_headers(headers),
                    payload,
                )
                return []

            # Exponential backoff with jitter for 429/5xx
            max_attempts = ADS_BACKOFF_MAX_ATTEMPTS
            base = ADS_BACKOFF_BASE_SECONDS
            resp = None
            for attempt in range(1, max_attempts + 1):
                with start_span("ads.search", {"component": "ads", "op": "search"}):
                    with time_ads_mutate():
                        self._bucket.acquire()
                        r = _post_with_timeout(url, headers=headers, json=payload)
                if r.status_code not in (429, 500, 502, 503, 504):
                    resp = r
                    break
                # Failure: record and backoff
                state = record_breaker_result(
                    "ads",
                    ok=False,
                    threshold=ADS_BREAKER_THRESHOLD,
                    cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                    error=f"{r.status_code}",
                )
                sleep_s = base * (2 ** (attempt - 1))
                jitter = random.uniform(0, sleep_s)  # nosec B311: non-crypto retry jitter
                time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                # If breaker tripped, stop early
                if state.get("open"):
                    break
            if resp is None:
                logging.error("Google Ads search aborted due to repeated failures or breaker open.")
                return []
            if not resp.ok:
                # Try to emit a clearer hint if the developer token is test-only
                try:
                    ej = resp.json()
                    details = ej.get("error", {}).get("details", [])
                    for d in details:
                        for e in d.get("errors", []):
                            auth = e.get("errorCode", {}).get("authorizationError")
                            if auth == "DEVELOPER_TOKEN_NOT_APPROVED":
                                logging.error(
                                    "Developer token is test-only. "
                                    "Use TEST accounts for login/customer IDs or request Standard Access. "
                                    "Validate-only does not bypass this."
                                )
                                break
                except Exception:
                    pass
                logging.error(f"Google Ads search error {resp.status_code} for {url}: {resp.text[:500]}")
                try:
                    inc_api_errors()
                except Exception:
                    pass
                try:
                    capture_error(
                        RuntimeError("ads_search_failed"),
                        tags={"component": "ads", "op": "search"},
                        extras={
                            "status": resp.status_code,
                            "url": url,
                            "headers": sanitize(headers),
                            "body": payload,
                            "resp": resp.text[:500],
                        },
                    )
                except Exception:
                    pass
                return []
            # Success resets breaker
            record_breaker_result(
                "ads",
                ok=True,
                threshold=ADS_BREAKER_THRESHOLD,
                cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
            )
            data = resp.json()
            results = data.get("results", [])
            campaigns = []
            for row in results:
                c = row.get("campaign", {})
                campaigns.append(
                    {
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "status": c.get("status"),
                        "channel": c.get("advertisingChannelType"),
                    }
                )
            if campaigns:
                logging.info("Found %d campaigns:", len(campaigns))
                for c in campaigns:
                    logging.info(
                        "- %s (ID=%s, status=%s, channel=%s)",
                        c["name"],
                        c["id"],
                        c["status"],
                        c.get("channel"),
                    )
            else:
                logging.info("No campaigns returned by search.")
            return campaigns
        except Exception as e:
            logging.error(f"Failed to list campaigns: {e}")
            return []

            def gaql(self, query: str) -> list[dict]:
                """Run a GAQL query against the Google Ads API and return raw results list.

                This is a thin helper used by the Reports UI for performance charts.
                """
                try:
                    open_now, until = is_breaker_open("ads")
                    if open_now:
                        logging.warning("Circuit breaker OPEN for Ads until %s; skipping GAQL.", until or "unknown")
                        return []
                    if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                        try:
                            self.credentials.refresh(Request())
                        except Exception as refresh_err:
                            logging.error(f"Failed to refresh OAuth token: {refresh_err}")
                            return []
                    if not DEVELOPER_TOKEN or not HAS_CUSTOMER_ID:
                        logging.error("Missing developer token or customer id for GAQL")
                        return []
                    url = f"{self.base_url}/customers/{CUSTOMER_ID}/googleAds:search"
                    headers = {
                        "Authorization": f"Bearer {self.credentials.token}",
                        "Content-Type": "application/json",
                        "developer-token": DEVELOPER_TOKEN,
                    }
                    if LOGIN_CUSTOMER_ID:
                        headers["login-customer-id"] = LOGIN_CUSTOMER_ID
                    payload = {"query": query}
                    # Backoff similar to list_campaigns
                    resp = None
                    for attempt in range(1, ADS_BACKOFF_MAX_ATTEMPTS + 1):
                        self._bucket.acquire()
                        r = _post_with_timeout(url, headers=headers, json=payload)
                        if r.status_code not in (429, 500, 502, 503, 504):
                            resp = r
                            break
                        state = record_breaker_result(
                            "ads",
                            ok=False,
                            threshold=ADS_BREAKER_THRESHOLD,
                            cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                            error=f"{r.status_code}",
                        )
                        sleep_s = ADS_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                        jitter = random.uniform(0, sleep_s)
                        time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                        if state.get("open"):
                            break
                    if resp is None:
                        logging.error("GAQL aborted due to retries/breaker")
                        return []
                    if not resp.ok:
                        logging.error("GAQL error %s: %s", resp.status_code, resp.text[:500])
                        try:
                            capture_error(
                                RuntimeError("gaql_failed"),
                                extras={
                                    "status": resp.status_code,
                                    "body": payload,
                                },
                            )
                        except Exception:
                            pass
                        return []
                    ej = resp.json()
                    return ej.get("results", []) or []
                except Exception as e:
                    logging.error("GAQL exception: %s", e)
                    try:
                        capture_error(e, tags={"component": "ads", "op": "gaql"})
                    except Exception:
                        pass
                    return []

    def diagnose_ads_access(self) -> _DiagResult:
        """Run a minimal GAQL search to diagnose connectivity and permissions.

        Returns a dict with keys:
          - ok: bool
          - status_code: int | None
          - error: str | None  (e.g., DEVELOPER_TOKEN_NOT_APPROVED)
          - message: str | None (server message)
          - hints: list[str]
        """
        result: _DiagResult = {"ok": False, "status_code": None, "error": None, "message": None, "hints": []}

        try:
            # Ensure valid access token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    result["message"] = f"Failed to refresh OAuth token: {refresh_err}"
                    result["hints"].append("Re-run auth: python -m src to refresh/create token.json")
                    return result

            # Basic config checks
            if not DEVELOPER_TOKEN:
                result["message"] = "Missing GOOGLE_ADS_DEVELOPER_TOKEN"
                result["hints"].append("Set GOOGLE_ADS_DEVELOPER_TOKEN in .env (or ADS_DEV_TOKEN)")
                return result
            if not HAS_CUSTOMER_ID:
                result["message"] = "Missing GOOGLE_ADS_CUSTOMER_ID"
                result["hints"].append("Set GOOGLE_ADS_CUSTOMER_ID (digits only, no dashes)")
                return result

            url = f"{self.base_url}/customers/{CUSTOMER_ID}/googleAds:search"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID

            # Minimal query
            payload = {"query": "SELECT customer.id FROM customer LIMIT 1"}

            self._bucket.acquire()
            resp = _post_with_timeout(url, headers=headers, json=payload)
            if resp.ok:
                result["ok"] = True
                result["message"] = "Google Ads API reachable and authorized for this CID"
                return result

            result["status_code"] = resp.status_code
            try:
                ej = resp.json()
            except Exception:
                ej = None
            if ej:
                err = ej.get("error", {})
                result["message"] = err.get("message")
                details = err.get("details", [])
                # Extract first specific error code if present
                found_code = None
                for d in details:
                    for e in d.get("errors", []):
                        code = e.get("errorCode", {})
                        for k, v in code.items():
                            if v:
                                found_code = v
                                break
                        if found_code:
                            break
                    if found_code:
                        break
                result["error"] = found_code

                # Hints based on common errors
                if found_code == "DEVELOPER_TOKEN_NOT_APPROVED":
                    result["hints"].append(
                        "Your developer token is test-only. "
                        "Use TEST accounts (login/customer IDs) or request Standard Access "
                        "in the Ads API Center."
                    )
                    if LOGIN_CUSTOMER_ID:
                        result["hints"].append(
                            "Ensure the login-customer-id is a TEST manager when using a test-only token."
                        )
                elif found_code in ("USER_PERMISSION_DENIED", "CUSTOMER_NOT_ENABLED"):
                    result["hints"].append("Verify the OAuth user has access to the customer account.")
                    result["hints"].append(
                        "If using a manager, set GOOGLE_ADS_LOGIN_CUSTOMER_ID to a manager CID "
                        "with access to the customer."
                    )
                elif found_code in ("CUSTOMER_NOT_FOUND", "INVALID_CUSTOMER_ID"):
                    result["hints"].append(
                        "Check GOOGLE_ADS_CUSTOMER_ID (digits only, no dashes) and that the account exists."
                    )
                elif found_code == "INVALID_LOGIN_CUSTOMER_ID":
                    result["hints"].append(
                        "Check GOOGLE_ADS_LOGIN_CUSTOMER_ID (digits only) and ensure it manages the customer."
                    )
                elif found_code == "AUTHENTICATION_ERROR":
                    result["hints"].append(
                        "Refresh OAuth credentials (delete secrets/token.json and re-auth if necessary)."
                    )

            else:
                result["message"] = f"HTTP {resp.status_code}: {resp.text[:200]}"

            # Generic hints
            if not result["hints"]:
                result["hints"].append("Enable DEBUG logs and retry to capture full server response.")
            return result
        except Exception as e:
            result["message"] = f"Unexpected error during diagnosis: {e}"
            result["hints"].append("Check network connectivity and environment variables.")
            return result

    def get_campaign_status(self, customer_id: str, campaign_id: str) -> str | None:
        try:
            # Circuit breaker guard
            open_now, until = is_breaker_open("ads")
            if open_now:
                logging.warning("Circuit breaker OPEN; skipping get_campaign_status.")
                return None
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception:
                    return None
            if not DEVELOPER_TOKEN:
                return None
            url = f"{self.base_url}/customers/{customer_id}/googleAds:search"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID
            # nosec B608: GAQL with numeric ID interpolation only; campaign_id comes from config/validated input
            q = f"SELECT campaign.status FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1"
            self._bucket.acquire()
            r = _post_with_timeout(url, headers=headers, json={"query": q})
            if not r.ok:
                return None
            rows = (r.json() or {}).get("results", [])
            if not rows:
                return None
            return rows[0].get("campaign", {}).get("status")
        except Exception:
            return None

    def set_campaign_status(
        self,
        new_status,
        customer_id: str | None = None,
        campaign_id: str | None = None,
        alert_id: int | None = None,
        validate_only: bool | None = None,
    ):
        try:
            # Resolve IDs (allow per-call override)
            cid = customer_id or CUSTOMER_ID
            camp_id = campaign_id or CAMPAIGN_ID

            # Circuit breaker guard
            open_now, until = is_breaker_open("ads")
            if open_now:
                logging.warning(
                    "Circuit breaker OPEN for Ads until %s; auto-switching to validate-only behavior.",
                    until or "unknown",
                )
                # If breaker is open, simulate validateOnly behavior (no live mutate)
                try:
                    record_action(alert_id, cid, camp_id, "status", None, new_status, "circuit_open", None)
                except Exception:
                    pass
                return True

            # Ensure we have a valid access token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    logging.error(f"Failed to refresh OAuth token: {refresh_err}")
                    return False

            if not DEVELOPER_TOKEN:
                logging.error("Missing GOOGLE_ADS_DEVELOPER_TOKEN. Set it in your .env and restart.")
                return False

            # Validate required IDs
            if not cid or not camp_id:
                logging.error("Missing GOOGLE_ADS_CUSTOMER_ID or GOOGLE_ADS_CAMPAIGN_ID in .env")
                return False

            # Optional safety: require Local Services channel
            if REQUIRE_LOCAL_SERVICES_ONLY:
                try:
                    url_info = f"{self.base_url}/customers/{cid}/googleAds:search"
                    headers_info = {
                        "Authorization": f"Bearer {self.credentials.token}",
                        "Content-Type": "application/json",
                        "developer-token": DEVELOPER_TOKEN,
                    }
                    if LOGIN_CUSTOMER_ID:
                        headers_info["login-customer-id"] = LOGIN_CUSTOMER_ID
                    # nosec B608: GAQL with numeric ID interpolation; camp_id validated upstream
                    q = (
                        "SELECT campaign.id, campaign.advertising_channel_type "
                        f"FROM campaign WHERE campaign.id = {camp_id} LIMIT 1"
                    )
                    self._bucket.acquire()
                    resp_info = _post_with_timeout(url_info, headers=headers_info, json={"query": q})
                    if resp_info.ok:
                        rows = (resp_info.json() or {}).get("results", [])
                        chan = None
                        if rows:
                            chan = rows[0].get("campaign", {}).get("advertisingChannelType")
                        if chan != "LOCAL_SERVICES":
                            logging.error(
                                "Safety guard: target campaign is not LOCAL_SERVICES (got %s). Aborting mutate.",
                                chan,
                            )
                            return False
                    else:
                        logging.error("Safety guard lookup failed (%s). Aborting mutate.", resp_info.status_code)
                        return False
                except Exception as guard_err:
                    logging.error("Safety guard encountered an error: %s", guard_err)
                    return False

            # If disabled for LSA, short-circuit to notify-only path when channel is LOCAL_SERVICES.
            # Allow runtime override via DB key 'lsa_mutate_via_ads_status'.
            _lsa_mutate_flag = LSA_MUTATE_VIA_ADS_STATUS
            try:
                _db_flag = get_config_value("lsa_mutate_via_ads_status")
                if isinstance(_db_flag, str) and _db_flag:
                    t = _db_flag.strip().lower()
                    if t in ("0", "false", "no"):
                        _lsa_mutate_flag = False
                    if t in ("1", "true", "yes"):
                        _lsa_mutate_flag = True
            except Exception:
                pass
            if not _lsa_mutate_flag:
                try:
                    info = self.get_campaign_status(cid, camp_id)
                except Exception:
                    info = None
                if info is not None:
                    # best-effort audit without mutate
                    try:
                        record_action(alert_id, cid, camp_id, "status", None, new_status, "lsa_notify_only", None)
                        record_audit_log(
                            who="system",
                            what="campaign.status",
                            why="lsa_notify_only",
                            old_value=None,
                            new_value=new_status,
                            request_id=None,
                            customer_id=cid,
                            campaign_id=camp_id,
                            alert_id=alert_id,
                            outcome="skipped",
                            error=None,
                        )
                    except Exception:
                        pass
                    logging.info("LSA_MUTATE_VIA_ADS_STATUS is false; skipping live mutate for campaign %s", camp_id)
                    return True

            # Correct REST URL for CampaignService.MutateCampaigns
            url = f"{self.base_url}/customers/{cid}/campaigns:mutate"
            # Determine validateOnly for this request: explicit override wins; otherwise use global flag
            use_validate_only = (validate_only is True) or (validate_only is None and VALIDATE_ONLY)
            if use_validate_only:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}validateOnly=true"

            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID

            # REST payload for a single update operation
            payload = {
                "operations": [
                    {
                        "update": {
                            "resourceName": f"customers/{cid}/campaigns/{camp_id}",
                            "status": "PAUSED" if new_status == "PAUSED" else "ENABLED",
                        },
                        # Field mask must be camelCase per Google Ads REST
                        "updateMask": "status",
                    }
                ]
            }

            if DRY_RUN:
                logging.info(
                    "[DRY_RUN] Would POST %s with headers=%s payload=%s",
                    url,
                    self._sanitized_headers(headers),
                    payload,
                )
                try:
                    record_action(alert_id, cid, camp_id, "status", None, new_status, "dry_run", None)
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="dry_run",
                        old_value=None,
                        new_value=new_status,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="dry_run",
                        error=None,
                        extras={"url": url},
                    )
                except Exception:
                    pass
                return True

            # Exponential backoff with jitter for 429/5xx
            max_attempts = ADS_BACKOFF_MAX_ATTEMPTS
            base = ADS_BACKOFF_BASE_SECONDS
            response = None
            for attempt in range(1, max_attempts + 1):
                with start_span("ads.mutate", {"component": "ads", "op": "mutate", "status": new_status}):
                    with time_ads_mutate():
                        self._bucket.acquire()
                        r = _post_with_timeout(url, headers=headers, json=payload)
                if r.status_code not in (429, 500, 502, 503, 504):
                    response = r
                    break
                # Failure: record and backoff
                state = record_breaker_result(
                    "ads",
                    ok=False,
                    threshold=ADS_BREAKER_THRESHOLD,
                    cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                    error=f"{r.status_code}",
                )
                sleep_s = base * (2 ** (attempt - 1))
                jitter = random.uniform(0, sleep_s)  # nosec B311: non-crypto retry jitter
                time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                # If breaker tripped, stop early
                if state.get("open"):
                    break
            if response is None:
                logging.error("Google Ads mutate aborted due to repeated failures or breaker open.")
                try:
                    record_action(
                        alert_id,
                        cid,
                        camp_id,
                        "status",
                        None,
                        new_status,
                        "aborted",
                        "retries_exhausted_or_breaker",
                    )
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="breakoff_or_retries",
                        old_value=None,
                        new_value=new_status,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="aborted",
                        error="retries_exhausted_or_breaker",
                        extras={"url": url},
                    )
                except Exception:
                    pass
                try:
                    capture_error(
                        RuntimeError("ads_mutate_aborted"),
                        tags={"component": "ads", "op": "mutate"},
                        extras={"url": url, "headers": sanitize(headers), "body": payload},
                    )
                except Exception:
                    pass
                return False
            if not response.ok:
                # Hint for test-only developer tokens
                try:
                    ej = response.json()
                    details = ej.get("error", {}).get("details", [])
                    for d in details:
                        for e in d.get("errors", []):
                            auth = e.get("errorCode", {}).get("authorizationError")
                            if auth == "DEVELOPER_TOKEN_NOT_APPROVED":
                                logging.error(
                                    "Developer token is test-only. Use TEST accounts for login/customer IDs or request "
                                    "Standard Access. Validate-only does not bypass this."
                                )
                                break
                except Exception:
                    pass
                logging.error(f"Google Ads error {response.status_code} for {url}: {response.text[:500]}")
                try:
                    inc_api_errors()
                except Exception:
                    pass
                try:
                    record_action(alert_id, cid, camp_id, "status", None, new_status, "error", response.text[:500])
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="ads_mutate_failed",
                        old_value=None,
                        new_value=new_status,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="error",
                        error=response.text[:500],
                        extras={"status": response.status_code},
                    )
                except Exception:
                    pass
                try:
                    capture_error(
                        RuntimeError("ads_mutate_failed"),
                        tags={"component": "ads", "op": "mutate"},
                        extras={
                            "status": response.status_code,
                            "url": url,
                            "headers": sanitize(headers),
                            "body": payload,
                            "resp": response.text[:500],
                        },
                    )
                except Exception:
                    pass
                response.raise_for_status()
            # Success
            if use_validate_only:
                logging.info("Validate-only successful for campaign %s -> %s", camp_id, new_status)
                try:
                    inc_ads_validate_ok()
                    record_action(alert_id, cid, camp_id, "status", None, new_status, "validate_only", None)
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="validate_only",
                        old_value=None,
                        new_value=new_status,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="validate_ok",
                        error=None,
                        extras={"url": url},
                    )
                except Exception:
                    pass
                # Do not reset breaker based on validate-only? Still consider it a successful call to the API.
                record_breaker_result(
                    "ads", ok=True, threshold=ADS_BREAKER_THRESHOLD, cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN
                )
                return True

            logging.info(f"Campaign status successfully set to {new_status}")
            try:
                inc_actions_applied()
            except Exception:
                pass
            # Success resets breaker
            record_breaker_result(
                "ads", ok=True, threshold=ADS_BREAKER_THRESHOLD, cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN
            )
            try:
                record_action(alert_id, cid, camp_id, "status", None, new_status, "ok", None)
                record_audit_log(
                    who="system",
                    what="campaign.status",
                    why="weather_trigger",
                    old_value=None,
                    new_value=new_status,
                    request_id=None,
                    customer_id=cid,
                    campaign_id=camp_id,
                    alert_id=alert_id,
                    outcome="ok",
                    error=None,
                )
            except Exception:
                pass
            return True

        except Exception as e:
            logging.error(f"Failed to update campaign status: {e}")
            return False

    def create_test_account(self):
        try:
            # Ensure valid token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    logging.error(f"Failed to refresh OAuth token: {refresh_err}")
                    return None

            if not DEVELOPER_TOKEN:
                logging.error("Missing GOOGLE_ADS_DEVELOPER_TOKEN.")
                return None
            if not LOGIN_CUSTOMER_ID:
                logging.error("GOOGLE_ADS_LOGIN_CUSTOMER_ID (manager) is required to create a test account.")
                return None

            # REST endpoint for CustomerService CreateCustomerClient
            url = f"{self.base_url}/customers/{LOGIN_CUSTOMER_ID}:createCustomerClient"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }

            # Only test managers can create test accounts. The request body uses 'customerClient' field.
            payload = {
                "customerClient": {
                    "descriptiveName": TEST_ACCOUNT_NAME,
                    "currencyCode": TEST_ACCOUNT_CURRENCY,
                    "timeZone": TEST_ACCOUNT_TIME_ZONE,
                    "trackingUrlTemplate": TEST_ACCOUNT_TRACKING_URL_TEMPLATE,
                    # This indicates a test account.
                    "testAccount": True,
                }
            }

            if DRY_RUN:
                logging.info(
                    "[DRY_RUN] Would POST %s with headers=%s payload=%s",
                    url,
                    self._sanitized_headers(headers),
                    payload,
                )
                return {"resourceName": "customers/TEST/placeholder"}

            self._bucket.acquire()
            resp = _post_with_timeout(url, headers=headers, json=payload)

            if not resp.ok:
                # Hint for test-only developer tokens
                try:
                    ej = resp.json()
                    details = ej.get("error", {}).get("details", [])
                    for d in details:
                        for e in d.get("errors", []):
                            auth = e.get("errorCode", {}).get("authorizationError")
                            if auth == "DEVELOPER_TOKEN_NOT_APPROVED":
                                logging.error(
                                    "Developer token is test-only. Only TEST managers can create test accounts. "
                                    "Switch to a TEST manager or request Standard Access."
                                )
                                break
                except Exception:
                    pass
                logging.error(
                    "Create test account error %s for %s: %s",
                    resp.status_code,
                    url,
                    resp.text[:500],
                )
                return None
            data = resp.json()
            resource_name = data.get("resourceName") or data.get("resource_name")
            logging.info("Created test account: %s", resource_name)
            return data
        except Exception as e:
            logging.error(f"Failed to create test account: {e}")
            return None

    def diagnose_ads_canary(self) -> _DiagResult:
        """Probe the canary API version with a minimal read-only query.

        Returns a dict like diagnose_ads_access().
        """
        result: _DiagResult = {"ok": False, "status_code": None, "error": None, "message": None, "hints": []}
        try:
            from .config.settings import API_VERSION_CANARY as _CAN

            if not _CAN:
                result["message"] = "API_VERSION_CANARY not set"
                result["hints"].append("Set GOOGLE_ADS_API_VERSION_CANARY to test a newer Ads API version.")
                return result
            # Ensure valid access token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    result["message"] = f"Failed to refresh OAuth token: {refresh_err}"
                    return result
            if not DEVELOPER_TOKEN:
                result["message"] = "Missing GOOGLE_ADS_DEVELOPER_TOKEN"
                return result
            if not HAS_CUSTOMER_ID:
                result["message"] = "Missing GOOGLE_ADS_CUSTOMER_ID"
                return result
            base = f"https://googleads.googleapis.com/{_CAN}"
            url = f"{base}/customers/{CUSTOMER_ID}/googleAds:search"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID
            payload = {"query": "SELECT customer.id FROM customer LIMIT 1"}
            self._bucket.acquire()
            resp = _post_with_timeout(url, headers=headers, json=payload)
            if resp.ok:
                result["ok"] = True
                result["message"] = f"Canary version {_CAN} reachable"
                return result
            result["status_code"] = resp.status_code
            try:
                ej = resp.json()
            except Exception:
                ej = None
            if ej:
                err = ej.get("error", {})
                result["message"] = err.get("message")
                details = err.get("details", [])
                found_code = None
                for d in details:
                    for e in d.get("errors", []):
                        code = e.get("errorCode", {})
                        for k, v in code.items():
                            if v:
                                found_code = v
                                break
                        if found_code:
                            break
                    if found_code:
                        break
                result["error"] = found_code
            return result
        except Exception as e:
            result["message"] = f"Unexpected error during canary diagnosis: {e}"
            return result

    def campaign_has_required_label(
        self,
        customer_id: str,
        campaign_id: str,
        required_labels: Optional[list[str]] | None = None,
    ) -> bool:
        """Return True if the campaign has at least one of the required labels.

        required_labels may be label names or label resource names (customers/{cid}/labels/{id}).
        If None or empty, returns True (no restriction).
        """
        try:
            if required_labels is not None:
                req = list(required_labels)
            else:
                req = list(REQUIRED_CAMPAIGN_LABELS) if REQUIRED_CAMPAIGN_LABELS else []
        except Exception:
            req = []
        if not req:
            return True

        try:
            # Circuit breaker guard
            open_now, _ = is_breaker_open("ads")
            if open_now:
                logging.warning("Circuit breaker OPEN; skipping label check and treating as missing.")
                return False

            # Ensure valid access token
            if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
                try:
                    self.credentials.refresh(Request())
                except Exception as refresh_err:
                    logging.error(f"Failed to refresh OAuth token: {refresh_err}")
                    return False

            if not DEVELOPER_TOKEN:
                logging.error("Missing GOOGLE_ADS_DEVELOPER_TOKEN.")
                return False

            url = f"{self.base_url}/customers/{customer_id}/googleAds:search"
            headers = {
                "Authorization": f"Bearer {self.credentials.token}",
                "Content-Type": "application/json",
                "developer-token": DEVELOPER_TOKEN,
            }
            if LOGIN_CUSTOMER_ID:
                headers["login-customer-id"] = LOGIN_CUSTOMER_ID

            # First, fetch label resource names attached to the campaign
            # nosec B608: GAQL with numeric ID interpolation; campaign_id validated upstream
            q_labels = f"SELECT campaign.id, campaign.labels FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1"
            payload = {"query": q_labels}

            # Backoff loop
            max_attempts = ADS_BACKOFF_MAX_ATTEMPTS
            base = ADS_BACKOFF_BASE_SECONDS
            resp = None
            for attempt in range(1, max_attempts + 1):
                with start_span("ads.search", {"component": "ads", "op": "search", "purpose": "labels"}):
                    with time_ads_mutate():
                        self._bucket.acquire()
                        r = _post_with_timeout(url, headers=headers, json=payload)
                if r.status_code not in (429, 500, 502, 503, 504):
                    resp = r
                    break
                state = record_breaker_result(
                    "ads",
                    ok=False,
                    threshold=ADS_BREAKER_THRESHOLD,
                    cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                    error=f"{r.status_code}",
                )
                sleep_s = base * (2 ** (attempt - 1))
                jitter = random.uniform(0, sleep_s)
                time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                if state.get("open"):
                    break
            if resp is None or not resp.ok:
                return False
            rows = (resp.json() or {}).get("results", [])
            if not rows:
                return False
            camp = rows[0].get("campaign", {})
            attached_rns = set(camp.get("labels") or [])
            if not attached_rns:
                return False

            # If required labels look like resource names, compare directly
            looks_like_rn = False
            for x in req:
                if "/labels/" in str(x):
                    looks_like_rn = True
                    break
            if looks_like_rn:
                req_rns = set()
                for x in req:
                    if isinstance(x, str) and "/labels/" in x:
                        req_rns.add(x)
                return bool(attached_rns & req_rns)

            # Otherwise, fetch names for the attached label resource names and compare by name
            # Build an IN clause for resource names
            rn_list_parts = []
            for rn in attached_rns:
                rn_list_parts.append(f"'{rn}'")
            rn_list = ", ".join(rn_list_parts)
            q_names = f"SELECT label.resource_name, label.name FROM label WHERE label.resource_name IN ({rn_list})"
            payload2 = {"query": q_names}
            resp2 = None
            for attempt in range(1, max_attempts + 1):
                with start_span("ads.search", {"component": "ads", "op": "search", "purpose": "label_names"}):
                    with time_ads_mutate():
                        self._bucket.acquire()
                        r2 = _post_with_timeout(url, headers=headers, json=payload2)
                if r2.status_code not in (429, 500, 502, 503, 504):
                    resp2 = r2
                    break
                state = record_breaker_result(
                    "ads",
                    ok=False,
                    threshold=ADS_BREAKER_THRESHOLD,
                    cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                    error=f"{r2.status_code}",
                )
                sleep_s = base * (2 ** (attempt - 1))
                jitter = random.uniform(0, sleep_s)
                time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                if state.get("open"):
                    break
            if resp2 is None or not resp2.ok:
                return False
            rows2 = (resp2.json() or {}).get("results", [])
            names = set()
            for row in rows2:
                lbl = row.get("label", {})
                n = lbl.get("name")
                if n:
                    names.add(n)
            return bool(names & set(req))
        except Exception as e:
            logging.info("Label check failed: %s", e)
            return False
