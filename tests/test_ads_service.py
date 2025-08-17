import pytest


# Reusable fake LSA client we can tweak per-test via attributes
class FakeLSAClient:
    def __init__(self, credentials):  # noqa: ARG002
        # Behavior controls
        self.status_sequence = ["PAUSED", "ENABLED"]
        self.set_calls = []
        self.get_calls = []
        self.set_returns = True

    def get_campaign_status(self, customer_id, campaign_id):  # noqa: ARG002
        self.get_calls.append((customer_id, campaign_id))
        if self.status_sequence:
            return self.status_sequence.pop(0)
        return "ENABLED"

    def set_campaign_status(self, new_status, *, customer_id=None, campaign_id=None, alert_id=None, validate_only=None):  # noqa: ARG002
        self.set_calls.append(
            {
                "status": new_status,
                "customer_id": customer_id,
                "campaign_id": campaign_id,
                "alert_id": alert_id,
                "validate_only": validate_only,
            }
        )
        return self.set_returns


@pytest.fixture
def env_defaults(monkeypatch):
    # Minimal required IDs; values won't be used by the fake client
    monkeypatch.setenv("GOOGLE_ADS_CUSTOMER_ID", "1234567890")
    monkeypatch.setenv("GOOGLE_ADS_CAMPAIGN_ID", "2222222222")
    # Keep global validate gate enabled by default
    monkeypatch.setenv("VALIDATE_GATE", "true")
    # Disable global dry-run/validate-only so the service logic drives behavior
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GOOGLE_ADS_VALIDATE_ONLY", "false")


def _patch_ads_service(monkeypatch):
    import src.services.ads_service as svc

    fake = FakeLSAClient(None)
    monkeypatch.setattr(svc, "LSAClient", lambda credentials: fake)
    # Avoid real sleeps during verify retries
    monkeypatch.setattr(svc, "_sleep", lambda *_a, **_k: None)
    # Make breaker checks closed by default
    monkeypatch.setattr(svc, "is_breaker_open", lambda name: (False, None))
    # Quiet any notification sends
    monkeypatch.setattr(svc.Notifier, "notify", lambda self, subject, body: None)
    return svc, fake


def test_ads_service_happy_path(dummy_creds, env_defaults, monkeypatch):
    svc, fake = _patch_ads_service(monkeypatch)
    # Sequence: before=PAUSED, apply ENABLED validate->live, verify sees ENABLED
    fake.status_sequence = ["PAUSED", "ENABLED"]

    s = svc.AdsService(dummy_creds)
    res = s.safe_set_campaign_status("ENABLED", customer_id="123", campaign_id="456", alert_id=1)

    assert res.ok is True
    assert res.before == "PAUSED"
    assert res.after == "ENABLED"
    assert res.rolled_back is False
    # Validate gate then live mutate captured
    assert any(call.get("validate_only") is True for call in fake.set_calls)
    assert any(call.get("validate_only") is False for call in fake.set_calls)


def test_ads_service_breaker_open_uses_validate_only(dummy_creds, env_defaults, monkeypatch):
    import src.services.ads_service as svc

    # Patch breaker to open and swap in fake client
    fake = FakeLSAClient(None)
    monkeypatch.setattr(svc, "LSAClient", lambda credentials: fake)
    monkeypatch.setattr(svc, "_sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(svc, "is_breaker_open", lambda name: (True, "soon"))
    monkeypatch.setattr(svc, "should_notify_breaker", lambda name, cooldown: True)
    monkeypatch.setattr(svc, "mark_breaker_notified", lambda name: None)
    monkeypatch.setattr(svc.Notifier, "notify", lambda self, subject, body: None)

    s = svc.AdsService(dummy_creds)
    res = s.safe_set_campaign_status("ENABLED", customer_id="123", campaign_id="456", alert_id=2)

    assert res.ok is True
    assert res.reason == "breaker_open"
    # Only validate-only mutate should be attempted in breaker-open path
    assert len(fake.set_calls) == 1
    assert fake.set_calls[0]["validate_only"] is True


def test_ads_service_verify_mismatch_rolls_back(dummy_creds, env_defaults, monkeypatch):
    svc, fake = _patch_ads_service(monkeypatch)
    # Sequence: before=PAUSED, after write verify returns PAUSED repeatedly
    fake.status_sequence = ["PAUSED", "PAUSED", "PAUSED", "PAUSED"]

    s = svc.AdsService(dummy_creds)
    res = s.safe_set_campaign_status("ENABLED", customer_id="123", campaign_id="456", alert_id=3)

    assert res.ok is False
    assert res.reason == "verify_mismatch"
    assert res.before == "PAUSED"
    # After may remain PAUSED due to mismatch during verification
    assert res.rolled_back is True
    # Expect at least two set calls: validate-only + live mutate + rollback
    statuses = [c["status"] for c in fake.set_calls]
    assert statuses.count("ENABLED") >= 1
    assert statuses.count("PAUSED") >= 1  # rollback


def test_ads_service_noop_when_already_in_target(dummy_creds, env_defaults, monkeypatch):
    svc, fake = _patch_ads_service(monkeypatch)
    # Already ENABLED before mutation
    fake.status_sequence = ["ENABLED"]

    s = svc.AdsService(dummy_creds)
    res = s.safe_set_campaign_status("ENABLED", customer_id="123", campaign_id="456", alert_id=4)

    assert res.ok is True
    assert res.reason == "noop"
    assert res.before == "ENABLED"
    assert res.after == "ENABLED"
    # Should not attempt a live mutate when already in state
    assert not any(call.get("validate_only") is False for call in fake.set_calls)
