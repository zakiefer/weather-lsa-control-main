def test_circuit_breaker_trip_and_cooldown(tmp_path):
    from src.db import ensure_schema, is_breaker_open, record_breaker_result

    ensure_schema()

    name = "ads"
    # Simulate failures to trip breaker (threshold=2, cooldown 1 minute)
    s1 = record_breaker_result(name, ok=False, threshold=2, cooldown_minutes=1, error="x")
    assert s1["open"] is False
    s2 = record_breaker_result(name, ok=False, threshold=2, cooldown_minutes=1, error="x")
    assert s2["open"] is True

    open_now, until = is_breaker_open(name)
    assert open_now is True
    assert until is not None

    # A success should reset the breaker
    s3 = record_breaker_result(name, ok=True, threshold=2, cooldown_minutes=1)
    assert s3["open"] is False
    open_now, _ = is_breaker_open(name)
    assert open_now is False
