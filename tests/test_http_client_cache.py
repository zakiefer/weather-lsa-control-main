from __future__ import annotations

from typing import Any

import ui.http_client as hc


class FakeResp:
    def __init__(self, status_code: int, data: Any):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        return FakeResp(200, {"url": url, "params": params or {}, "n": self.calls})


def test_fetch_json_uses_ttl_cache(monkeypatch):
    # fresh caches
    hc.clear_caches(clear_status=True)

    fake = FakeClient()
    monkeypatch.setattr(hc, "_CLIENT", fake)

    url = "https://example.com/test.json"

    first = hc.fetch_json(url, ttl=120)
    assert first and first["n"] == 1

    # second call with same key should hit cache (no client.get call)
    second = hc.fetch_json(url, ttl=120)
    assert second == first
    assert fake.calls == 1, "should not call client on cache hit"

    # status snapshot should indicate from_cache on the latest
    snap = hc.get_status_snapshot()
    assert snap, "expected at least one status entry"
    latest = snap[0]
    assert latest.get("url") == url
    assert latest.get("from_cache") is True
