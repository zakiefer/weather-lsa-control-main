from src.health import compute_readiness


def test_compute_readiness_shape():
    info = compute_readiness()
    # Required keys exist
    for k in [
        "ready",
        "db_ok",
        "creds_ok",
        "breaker_open",
        "queue",
        "smtp",
        "clock",
        "time",
    ]:
        assert k in info
    assert isinstance(info["ready"], bool)
    assert isinstance(info["db_ok"], bool)
    assert isinstance(info["creds_ok"], bool)
    assert isinstance(info["breaker_open"], bool)
    assert isinstance(info["queue"], dict)
    assert isinstance(info["smtp"], dict)
    # clock may be empty dict if probe fails; still a dict
    assert isinstance(info["clock"], dict)
