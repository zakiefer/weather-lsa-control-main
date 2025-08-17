import pytest


def _resp_ok(data):
    class R:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return data

    return R()


def _resp_err(status=500, text="err"):
    _t = text
    _s = status

    class R:
        ok = False
        status_code = _s
        text = _t

        def json(self):
            return {"error": {"message": _t}}

    return R()


@pytest.fixture
def patch_common(monkeypatch):
    import src.lsa_client as lc

    # Ensure breaker closed and token available
    monkeypatch.setattr(lc, "DEVELOPER_TOKEN", "devtoken")
    monkeypatch.setattr(lc, "LOGIN_CUSTOMER_ID", "")
    monkeypatch.setattr(lc, "is_breaker_open", lambda name: (False, None))
    return lc


def test_lsa_only_guard_blocks_non_lsa_mutate(dummy_creds, patch_common, monkeypatch):
    lc = patch_common
    # Enforce LSA-only
    monkeypatch.setattr(lc, "REQUIRE_LOCAL_SERVICES_ONLY", True)

    calls = []

    def fake_post(url, headers=None, json=None):  # noqa: ARG001
        calls.append(url)
        if url.endswith("googleAds:search"):
            # Guard lookup for advertising_channel_type returns non-LSA
            return _resp_ok({"results": [{"campaign": {"id": "456", "advertisingChannelType": "SEARCH"}}]})
        if url.endswith("campaigns:mutate"):
            return _resp_err(400, "should_not_be_called")
        return _resp_ok({})

    monkeypatch.setattr(lc.requests, "post", fake_post)

    client = lc.LSAClient(dummy_creds)
    ok = client.set_campaign_status("ENABLED", customer_id="123", campaign_id="456", alert_id=1, validate_only=False)

    assert ok is False
    # Verify mutate endpoint was never called due to guard
    assert not any(u.endswith("campaigns:mutate") for u in calls)


def test_campaign_has_required_label_by_name(dummy_creds, patch_common, monkeypatch):
    lc = patch_common
    # Require a specific label name
    monkeypatch.setattr(lc, "REQUIRED_CAMPAIGN_LABELS", ["SAFE-LSA"])

    def fake_post(url, headers=None, json=None):  # noqa: ARG001
        q = (json or {}).get("query", "")
        if "campaign.labels" in q:
            # First call: return attached label resource names
            return _resp_ok(
                {
                    "results": [
                        {
                            "campaign": {
                                "id": "456",
                                "labels": [
                                    "customers/123/labels/999",
                                    "customers/123/labels/1001",
                                ],
                            }
                        }
                    ]
                }
            )
        if "FROM label" in q:
            # Second call: map resource names to label names
            return _resp_ok(
                {
                    "results": [
                        {"label": {"resource_name": "customers/123/labels/999", "name": "SAFE-LSA"}},
                        {"label": {"resource_name": "customers/123/labels/1001", "name": "OTHER"}},
                    ]
                }
            )
        return _resp_ok({})

    monkeypatch.setattr(lc.requests, "post", fake_post)

    client = lc.LSAClient(dummy_creds)
    ok = client.campaign_has_required_label("123", "456", required_labels=None)
    assert ok is True
