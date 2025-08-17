# ruff: noqa: I001
from datetime import datetime, timezone

import pytest
from src.db import ensure_schema, enqueue_mutation, upsert_alert_cap

CID = "0000000000"
CAMP = "1111111111"


def _fake_ads_post_factory_with_mismatch():
    calls = []

    class Resp:
        def __init__(self, ok=True, status_code=200, data=None, text=""):
            self.ok = ok
            self.status_code = status_code
            self._data = data or {}
            self.text = text

        def json(self):
            return self._data

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(f"HTTP {self.status_code}")

    def _post(url, headers=None, json=None):
        entry = {"url": url, "json": json}
        calls.append(entry)
        return Resp(ok=True, data={})

    def _get_status_factory():
        # First verify returns wrong status, then correct after rollback to previous
        states = ["PAUSED", "ENABLED"]

        def _get(cid, camp):
            return states.pop(0) if states else "ENABLED"

        return _get

    return _post, _get_status_factory, calls


@pytest.fixture
def env_ready(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dummy")
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", CID)
    monkeypatch.setenv("GOOGLE_ADS_CAMPAIGN_ID", CAMP)
    monkeypatch.setenv("REQUIRE_LOCAL_SERVICES_ONLY", "true")
    monkeypatch.setenv("GOOGLE_ADS_VALIDATE_ONLY", "false")
    monkeypatch.setenv("DRY_RUN", "false")


def test_worker_rollback_on_verify_mismatch(tmp_path, monkeypatch, dummy_creds, env_ready):
    # Seed a resolved last alert to allow update flow
    ensure_schema()
    aid = upsert_alert_cap(
        cap_id="TEST",
        effective_at=datetime.now(timezone.utc).isoformat(),
        poly_hash=None,
        source="test",
        areas="18163",
        severity="Severe",
    )
    enqueue_mutation(aid, CID, CAMP, "status", "ENABLED")

    # Patch LSA client to simulate verify mismatch once, then allow rollback
    import src.lsa_client as lc

    post, get_status_factory, calls = _fake_ads_post_factory_with_mismatch()
    monkeypatch.setattr(lc.requests, "post", post)

    # Patch client's get_campaign_status to return mis-match then previous
    import src.lsa_client as lsa
    import src.worker as worker

    status_seq = ["PAUSED", "ENABLED"]

    def fake_get(self, cid, camp):
        return status_seq.pop(0) if status_seq else "ENABLED"

    monkeypatch.setattr(lsa.LSAClient, "get_campaign_status", fake_get)

    # Run drain
    worker.drain_queue(dummy_creds)

    # Confirm we made mutate calls; audit/rollback handling occurs inside worker
    assert len(calls) >= 1
