"""Ads service orchestrating safe campaign status updates.

This layer reduces duplication by centralizing:
- breaker gating and one-shot notify
- validate-only gate
- read-after-write verification with retry
- rollback on mismatch with metrics/notifications/audit hooks via db
- LSA-only and required-label guardrails
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import sleep as _sleep

from ..config import settings as cfg
from ..db import is_breaker_open, mark_breaker_notified, record_audit_log, should_notify_breaker
from ..lsa_client import LSAClient
from ..metrics import inc_ads_rollbacks, inc_ads_verify_mismatch
from ..notifier import Notifier


@dataclass
class MutationResult:
    ok: bool
    before: str | None = None
    after: str | None = None
    rolled_back: bool = False
    reason: str | None = None


class AdsService:
    def __init__(self, credentials):
        self.client = LSAClient(credentials)
        self.notifier = Notifier()

    def _notify_breaker_once(self, until) -> None:
        try:
            if should_notify_breaker("ads", getattr(cfg, "ADS_BREAKER_NOTIFY_COOLDOWN_MIN", 60)):
                self.notifier.notify(
                    subject="LSA Circuit Breaker OPEN",
                    body=(
                        f"Ads API breaker is open until {until or 'unknown'}. Auto-switching to validate-only behavior."
                    ),
                )
                mark_breaker_notified("ads")
        except Exception:
            pass

    def safe_set_campaign_status(
        self,
        target: str,
        *,
        customer_id: str | None,
        campaign_id: str | None,
        alert_id: int | None = None,
        use_validate_gate: bool | None = None,
    ) -> MutationResult:
        """Apply status with validate gate, verify, and rollback.

        Returns MutationResult capturing before/after and rollback outcome.
        """
        try:
            # Breaker gating (notify once)
            open_now, until = is_breaker_open("ads")
            if open_now:
                logging.warning("Circuit breaker open until %s; skipping live mutate.", until or "unknown")
                self._notify_breaker_once(until)
                # Record validate-only gate path through client (will audit appropriately)
                ok = self.client.set_campaign_status(
                    target,
                    customer_id=customer_id,
                    campaign_id=campaign_id,
                    alert_id=alert_id,
                    validate_only=True,
                )
                return MutationResult(ok=bool(ok), before=None, after=None, rolled_back=False, reason="breaker_open")

            # Label/LSA-only guard is enforced inside client; here we proceed with gate
            gate = getattr(cfg, "VALIDATE_GATE", True) if use_validate_gate is None else bool(use_validate_gate)
            cid = customer_id or cfg.CUSTOMER_ID
            camp_id = campaign_id or cfg.CAMPAIGN_ID

            if gate:
                # 1) Validate-only first
                ok = self.client.set_campaign_status(
                    target,
                    customer_id=customer_id,
                    campaign_id=campaign_id,
                    alert_id=alert_id,
                    validate_only=True,
                )
                if not ok:
                    return MutationResult(ok=False, reason="validate_failed")

            # 2) Read current
            current = self.client.get_campaign_status(cid, camp_id)
            if current == target:
                try:
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="already_in_state",
                        old_value=current,
                        new_value=target,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="noop",
                        error=None,
                        extras=None,
                    )
                except Exception:
                    pass
                return MutationResult(ok=True, before=current, after=current, rolled_back=False, reason="noop")

            # 3) Apply live change
            ok = self.client.set_campaign_status(
                target,
                customer_id=customer_id,
                campaign_id=campaign_id,
                alert_id=alert_id,
                validate_only=False,
            )
            if not ok:
                return MutationResult(ok=False, before=current, after=None, rolled_back=False, reason="mutate_failed")

            # 4) Verify after write (with retries)
            attempts = 0
            after = None
            while attempts < 3:
                after = self.client.get_campaign_status(cid, camp_id)
                if after == target:
                    break
                _sleep(2)
                attempts += 1

            if after == target:
                return MutationResult(ok=True, before=current, after=after, rolled_back=False, reason=None)

            # Verify mismatch → rollback
            try:
                inc_ads_verify_mismatch()
            except Exception:
                pass
            try:
                record_audit_log(
                    who="system",
                    what="campaign.status",
                    why="verify_mismatch",
                    old_value=current,
                    new_value=target,
                    request_id=None,
                    customer_id=cid,
                    campaign_id=camp_id,
                    alert_id=alert_id,
                    outcome="error",
                    error="read_after_write_mismatch",
                    extras={"attempts": attempts},
                )
            except Exception:
                pass

            rollback_ok = bool(
                self.client.set_campaign_status(
                    current,
                    customer_id=customer_id,
                    campaign_id=campaign_id,
                    alert_id=alert_id,
                    validate_only=False,
                )
            )
            try:
                inc_ads_rollbacks()
            except Exception:
                pass
            try:
                if rollback_ok:
                    self.notifier.notify(
                        subject="LSA rollback applied after verify mismatch",
                        body=(
                            f"Rolled back campaign {camp_id} in customer {cid} "
                            f"from target {target} to previous {current} after verification mismatch."
                        ),
                    )
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="rollback",
                        old_value=target,
                        new_value=current,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="rollback_ok",
                        error=None,
                        extras=None,
                    )
                else:
                    self.notifier.notify(
                        subject="LSA rollback FAILED after verify mismatch",
                        body=(
                            f"Failed to rollback campaign {camp_id} in customer {cid} "
                            f"to {current} after verification mismatch (target {target})."
                        ),
                    )
                    record_audit_log(
                        who="system",
                        what="campaign.status",
                        why="rollback",
                        old_value=target,
                        new_value=current,
                        request_id=None,
                        customer_id=cid,
                        campaign_id=camp_id,
                        alert_id=alert_id,
                        outcome="rollback_failed",
                        error="rollback_failed",
                        extras=None,
                    )
            except Exception:
                pass
            return MutationResult(
                ok=False,
                before=current,
                after=after,
                rolled_back=rollback_ok,
                reason="verify_mismatch",
            )
        except Exception as e:
            logging.error("safe_set_campaign_status failed: %s", e)
            return MutationResult(ok=False, reason="exception")
