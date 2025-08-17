import json
import os
import tempfile

from src.rules import evaluate, load_rules


def write_json(tmp, data):
    p = os.path.join(tmp, "rules.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


def test_evaluate_basic_enable():
    data = {"rules": [{"name": "sev_extreme", "severities": ["Extreme"], "action": "ENABLE"}]}
    with tempfile.TemporaryDirectory() as tmp:
        path = write_json(tmp, data)
        rules = load_rules(path)
        act = evaluate(rules, severity="Extreme", event=None, counties_fips=["18163"], alert_age_minutes=5)
        assert act == "ENABLE"


def test_evaluate_duration_pauses():
    data = {"rules": [{"name": "cooldown", "min_duration": "60m", "action": "PAUSE"}]}
    with tempfile.TemporaryDirectory() as tmp:
        path = write_json(tmp, data)
        rules = load_rules(path)
        # Under 60m -> no match
        assert evaluate(rules, severity=None, event=None, counties_fips=[], alert_age_minutes=30) is None
        # 60m+ -> PAUSE
        assert evaluate(rules, severity=None, event=None, counties_fips=[], alert_age_minutes=61) == "PAUSE"


def test_evaluate_counties_noop():
    data = {"rules": [{"name": "skip_others", "counties": ["18163", "18173"], "action": "NOOP"}]}
    with tempfile.TemporaryDirectory() as tmp:
        path = write_json(tmp, data)
        rules = load_rules(path)
        assert evaluate(rules, severity=None, event=None, counties_fips=["18163"], alert_age_minutes=None) == "NOOP"
        assert evaluate(rules, severity=None, event=None, counties_fips=["99999"], alert_age_minutes=None) is None
