# ruff: noqa: I001
from src.db import enqueue_mutation, ensure_schema
from src.worker import drain_queue


def test_quiet_hours_suppresses_mutations(monkeypatch, dummy_creds, tmp_path):
    # Set quiet hours surrounding a known time

    ensure_schema()
    # Queue a dummy job
    enqueue_mutation(
        alert_id=None,
        customer_id="0000000000",
        campaign_id="1111111111",
        action="status",
        new_status="ENABLED",
    )

    # Set quiet hours to full day to guarantee suppression
    monkeypatch.setenv("QUIET_HOURS", "00:00-23:59")

    # Patch LSA client to ensure no network is attempted in this test environment
    import src.lsa_client as lc

    def fail(*a, **k):
        raise AssertionError("No mutate calls should occur during quiet hours")

    monkeypatch.setattr(lc.requests, "post", fail)

    # Run drain; it should exit early due to quiet hours and leave queue untouched
    drain_queue(dummy_creds)
    from src.db import get_queue_stats

    stats = get_queue_stats()
    assert stats.get("queued", 0) >= 1
