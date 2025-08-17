import logging

from .config import settings as cfg
from .db import (
    acquire_instance_lock,
    ensure_schema,
    fetch_next_mutation,
    get_queue_length,
    mark_mutation_done,
    mark_mutation_started,
    release_instance_lock,
    requeue_mutation,
)
from .services.ads_service import AdsService
from .utils.time import within_quiet_hours


def drain_queue(credentials) -> int:
    """Drain queued mutations sequentially with safety checks. Returns processed count."""
    ensure_schema()
    lock = acquire_instance_lock("worker")
    if not lock.get("ok"):
        logging.warning("Another worker is active; skipping drain.")
        return 0
    processed = 0
    try:
        svc = AdsService(credentials)
        from .db import is_daily_mutation_limit_reached

        while True:
            job = fetch_next_mutation()
            if not job:
                break
            # Kill switch
            try:
                if getattr(cfg, "KILL_SWITCH", False):
                    logging.warning("KILL_SWITCH is active; stopping worker.")
                    break
            except Exception:
                pass
            # Quiet hours
            try:
                qh = getattr(cfg, "QUIET_HOURS", "") or ""
                if qh and within_quiet_hours(qh):
                    logging.info("Within QUIET_HOURS %s; stopping worker until window ends.", qh)
                    break
            except Exception:
                pass
            try:
                if is_daily_mutation_limit_reached(int(getattr(cfg, "MAX_MUTATIONS_PER_DAY", 0) or 0)):
                    logging.warning("Daily mutation limit reached; stopping worker for today.")
                    break
            except Exception:
                pass
            processed += 1
            mid = job["id"]
            mark_mutation_started(mid)
            try:
                ok = True
                if job["action"] == "status":
                    target = job["new_status"]
                    res = svc.safe_set_campaign_status(
                        target,
                        customer_id=job["customer_id"],
                        campaign_id=job["campaign_id"],
                        alert_id=job["alert_id"],
                    )
                    ok = res.ok
                else:
                    ok = True
                if ok:
                    mark_mutation_done(mid)
                else:
                    # requeue with small delay on transient failure path
                    requeue_mutation(mid, delay_seconds=30, error="mutate_failed")
            except Exception as e:
                # requeue with backoff; simple fixed delay here (Ads client already backs off)
                requeue_mutation(mid, delay_seconds=60, error=str(e)[:500])
        logging.info("Queue drained. processed=%d queued_remaining=%d", processed, get_queue_length())
        return processed
    finally:
        release_instance_lock(lock.get("token"))
