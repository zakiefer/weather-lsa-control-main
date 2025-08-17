from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests
from google.auth.transport.requests import Request

from .config.settings import (
    ADS_BACKOFF_BASE_SECONDS,
    ADS_BACKOFF_MAX_ATTEMPTS,
    ADS_BACKOFF_MAX_SLEEP,
    ADS_BREAKER_COOLDOWN_MIN,
    ADS_BREAKER_THRESHOLD,
    ADS_BURST,
    ADS_QPS,
)
from .db import is_breaker_open, record_breaker_result
from .observability import capture_error, sanitize
from .ratelimit import TokenBucket

BASE_URL = "https://localservices.googleapis.com/v1"


def _to_ymd(d: date) -> tuple[int, int, int]:
    return (d.year, d.month, d.day)


def _mask_phone(p: str | None) -> str | None:
    if not p:
        return None
    s = "".join(ch for ch in p if ch.isdigit())
    if len(s) < 4:
        return "***"
    return f"***{s[-4:]}"


@dataclass
class Lead:
    google_ads_lead_id: str | None
    account_id: str | None
    business_name: str | None
    created_at: str | None
    lead_type: str | None
    lead_category: str | None
    geo: str | None
    charge_status: str | None
    lead_price: float | None
    currency_code: str | None
    dispute_status: str | None
    job_type: str | None
    postal_code: str | None
    phone_last4: str | None


class LSAReportingClient:
    """Read-only client for Local Services Ads reporting endpoints.

    Auth uses the same OAuth credentials (adwords scope). No developer token header is required.
    """

    def __init__(self, credentials):
        self.credentials = credentials
        self._bucket = TokenBucket(rate_per_sec=ADS_QPS, burst=ADS_BURST)

    def _authz(self) -> dict[str, str]:
        if getattr(self.credentials, "expired", False) and getattr(self.credentials, "refresh_token", None):
            try:
                self.credentials.refresh(Request())
            except Exception as e:
                logging.error("Failed to refresh OAuth token for LSA reporting: %s", e)
                raise
        return {"Authorization": f"Bearer {self.credentials.token}"}

    def _get(self, path: str, params: dict[str, Any]):
        # Circuit breaker
        open_now, until = is_breaker_open("lsa_reports")
        if open_now:
            logging.warning("LSA reporting breaker OPEN until %s; skipping GET %s", until or "unknown", path)
            return None

        url = f"{BASE_URL}/{path}"
        headers = self._authz()
        # Backoff on 429/5xx
        resp = None
        for attempt in range(1, ADS_BACKOFF_MAX_ATTEMPTS + 1):
            try:
                self._bucket.acquire()
                r = requests.get(url, headers=headers, params=params, timeout=30)
            except Exception as e:
                # Treat as retryable network error
                state = record_breaker_result(
                    "lsa_reports",
                    ok=False,
                    threshold=ADS_BREAKER_THRESHOLD,
                    cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN,
                    error=str(e)[:120],
                )
                sleep_s = ADS_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                jitter = random.uniform(0, sleep_s)
                time.sleep(min(ADS_BACKOFF_MAX_SLEEP, sleep_s + jitter))
                if state.get("open"):
                    break
                continue

            if r.status_code not in (429, 500, 502, 503, 504):
                resp = r
                break
            # Record failure and backoff
            state = record_breaker_result(
                "lsa_reports",
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
            logging.error("LSA reporting GET aborted due to retries/breaker: %s", url)
            try:
                capture_error(RuntimeError("lsa_reporting_aborted"), extras={"url": url, "params": params})
            except Exception:
                pass
            return None

        if not resp.ok:
            logging.error("LSA reporting error %s for %s: %s", resp.status_code, url, resp.text[:400])
            try:
                capture_error(
                    RuntimeError("lsa_reporting_failed"),
                    extras={
                        "status": resp.status_code,
                        "url": url,
                        "params": sanitize(params),
                        "resp": resp.text[:400],
                    },
                )
            except Exception:
                pass
            return None

        # Success resets breaker
        record_breaker_result(
            "lsa_reports", ok=True, threshold=ADS_BREAKER_THRESHOLD, cooldown_minutes=ADS_BREAKER_COOLDOWN_MIN
        )
        return resp.json()

    def search_detailed_leads(
        self,
        *,
        start: date,
        end: date,
        account_id: str | None = None,
        manager_customer_id: str | None = None,
        page_size: int = 250,
        max_pages: int = 10,
    ) -> list[Lead]:
        """Search detailed leads using the LSA reporting API.

        Tries accountId variants when provided (accounts/{CID} vs CID, ':' vs '=')
        and falls back to manager_customer_id if accountId is not supplied.
        """
        if not (account_id or manager_customer_id):
            logging.warning("Neither LSA account_id nor manager_customer_id provided for LSA detailed leads.")
            return []

        y1, m1, d1 = _to_ymd(start)
        y2, m2, d2 = _to_ymd(end)

        # Build query string per docs: requires manager_customer_id; optional customer_id
        mgr = str(manager_customer_id).replace("-", "").strip() if manager_customer_id else ""
        cust = None
        if account_id:
            cust = str(account_id).replace("accounts/", "").replace("-", "").strip()
        parts = []
        if mgr:
            parts.append(f"manager_customer_id:{mgr}")
        if cust:
            parts.append(f"customer_id:{cust}")
        query = ";".join(parts) if parts else None

        params: dict[str, Any] = {
            "query": query,
            "startDate.year": y1,
            "startDate.month": m1,
            "startDate.day": d1,
            "endDate.year": y2,
            "endDate.month": m2,
            "endDate.day": d2,
            "pageSize": min(max(page_size, 1), 10000),
        }
        path = "detailedLeadReports:search"
        results: list[Lead] = []
        page = 0
        next_token: str | None = None
        tried_variants = 0

        while page < max_pages:
            page += 1
            if next_token:
                params["pageToken"] = next_token
            data = self._get(path, params)
            if not data:
                # Try manager-only (drop customer_id) once if we had both
                if cust and tried_variants < 1 and mgr:
                    params.pop("pageToken", None)
                    params["query"] = f"manager_customer_id:{mgr}"
                    tried_variants += 1
                    page -= 1
                    continue
                break

            page_rows = data.get("detailedLeadReports", []) or []
            if page == 1 and not page_rows and cust and tried_variants < 1 and mgr:
                # Some tenants return an empty array with customer filter; retry manager-only once
                params.pop("pageToken", None)
                params["query"] = f"manager_customer_id:{mgr}"
                tried_variants += 1
                page -= 1
                continue

            for row in page_rows:
                msg = row.get("messageLead") or {}
                ph = row.get("phoneLead") or {}
                book = row.get("bookingLead") or {}
                job_type = msg.get("jobType") or book.get("jobType")
                postal = msg.get("postalCode") or None
                phone = (
                    ph.get("consumerPhoneNumber") or msg.get("consumerPhoneNumber") or book.get("consumerPhoneNumber")
                )
                results.append(
                    Lead(
                        google_ads_lead_id=str(row.get("googleAdsLeadId") or ""),
                        account_id=str(row.get("accountId") or ""),
                        business_name=row.get("businessName"),
                        created_at=row.get("leadCreationTimestamp"),
                        lead_type=row.get("leadType"),
                        lead_category=row.get("leadCategory"),
                        geo=row.get("geo"),
                        charge_status=row.get("chargeStatus"),
                        lead_price=float(row.get("leadPrice")) if row.get("leadPrice") is not None else None,
                        currency_code=row.get("currencyCode"),
                        dispute_status=row.get("disputeStatus"),
                        job_type=job_type,
                        postal_code=postal,
                        phone_last4=_mask_phone(phone),
                    )
                )
            next_token = data.get("nextPageToken")
            if not next_token:
                break

        return results

    def search_account_reports(
        self,
        *,
        start: date,
        end: date,
        account_id: str | None = None,
        manager_customer_id: str | None = None,
        page_size: int = 250,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        if not (account_id or manager_customer_id):
            logging.warning("Neither LSA account_id nor manager_customer_id provided for LSA account reports.")
            return []

        y1, m1, d1 = _to_ymd(start)
        y2, m2, d2 = _to_ymd(end)

        mgr = str(manager_customer_id).replace("-", "").strip() if manager_customer_id else ""
        cust = None
        if account_id:
            cust = str(account_id).replace("accounts/", "").replace("-", "").strip()
        parts = []
        if mgr:
            parts.append(f"manager_customer_id:{mgr}")
        if cust:
            parts.append(f"customer_id:{cust}")
        query = ";".join(parts) if parts else None

        params: dict[str, Any] = {
            "query": query,
            "startDate.year": y1,
            "startDate.month": m1,
            "startDate.day": d1,
            "endDate.year": y2,
            "endDate.month": m2,
            "endDate.day": d2,
            "pageSize": min(max(page_size, 1), 10000),
        }

        path = "accountReports:search"
        rows: list[dict[str, Any]] = []
        page = 0
        next_token: str | None = None
        retried_alt = False
        while page < max_pages:
            page += 1
            if next_token:
                params["pageToken"] = next_token
            data = self._get(path, params)
            if not data:
                # Try manager-only once if we included a customer filter
                if cust and not retried_alt and mgr:
                    params.pop("pageToken", None)
                    params["query"] = f"manager_customer_id:{mgr}"
                    retried_alt = True
                    page -= 1
                    continue
                break
            for r in data.get("accountReports", []) or []:
                rows.append(r)
            next_token = data.get("nextPageToken")
            if not next_token:
                break
        return rows
