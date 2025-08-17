from src.lsa_client import _post_with_timeout


def test_post_with_timeout_falls_back(monkeypatch):
    calls = {"count": 0}

    class Resp:
        def __init__(self):
            self.ok = True
            self.status_code = 200

        def json(self):
            return {"ok": True}

    def fake_post_timeout(url, headers=None, json=None, timeout=None):  # noqa: ARG001
        calls["count"] += 1
        # Simulate stub that rejects timeout kwarg
        raise TypeError("unexpected keyword argument 'timeout'")

    def fake_post_no_timeout(url, headers=None, json=None):  # noqa: ARG001
        calls["count"] += 1
        return Resp()

    import src.lsa_client as lc

    monkeypatch.setattr(lc.requests, "post", fake_post_timeout)

    # First call will raise TypeError; wrapper should fall back and try again without timeout
    # To simulate the fallback, switch the monkeypatch to the no-timeout version on second call
    def side_effect(url, headers=None, json=None, timeout=None):  # noqa: ARG001
        monkeypatch.setattr(lc.requests, "post", fake_post_no_timeout)
        raise TypeError("unexpected keyword argument 'timeout'")

    monkeypatch.setattr(lc.requests, "post", side_effect)

    resp = _post_with_timeout("http://example.com", headers={}, json={})
    assert resp.ok
    assert calls["count"] >= 1
