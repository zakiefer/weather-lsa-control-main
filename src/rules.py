import json
import os
from dataclasses import dataclass
from typing import Any, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass
class Rule:
    name: str
    severities: Optional[list[str]] = None
    events: Optional[list[str]] = None
    counties: Optional[list[str]] = None  # list of 5-digit FIPS
    min_duration_minutes: Optional[int] = None  # require alert older than this
    action: str = "ENABLE"  # ENABLE or PAUSE or NOOP

    def matches(
        self,
        *,
        severity: Optional[str],
        event: Optional[str],
        counties_fips: list[str],
        alert_age_minutes: Optional[int],
    ) -> bool:
        if self.severities and severity and severity not in self.severities:
            return False
        if self.events and event and event not in self.events:
            return False
        if self.counties:
            if not any(c in (counties_fips or []) for c in self.counties):
                return False
        if self.min_duration_minutes is not None and alert_age_minutes is not None:
            if alert_age_minutes < self.min_duration_minutes:
                return False
        return True


def _parse_duration(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        try:
            if s.endswith("m"):
                return int(s[:-1])
            if s.endswith("h"):
                return int(float(s[:-1]) * 60)
            return int(s)
        except Exception:
            return None
    return None


def load_rules(path: str) -> list[Rule]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    with open(path, encoding="utf-8") as f:
        if ext in (".yaml", ".yml"):
            if yaml is None:
                raise RuntimeError("PyYAML not installed. Add pyyaml to requirements.txt or use JSON.")
            data = yaml.safe_load(f) or {}
        else:
            data = json.load(f)
    items = data.get("rules") if isinstance(data, dict) else data
    rules: list[Rule] = []
    for i, item in enumerate(items or []):
        name = item.get("name") or f"rule_{i + 1}"
        rules.append(
            Rule(
                name=name,
                severities=item.get("severities") or None,
                events=item.get("events") or None,
                counties=item.get("counties") or None,
                min_duration_minutes=_parse_duration(item.get("min_duration")),
                action=(item.get("action") or "ENABLE").upper(),
            )
        )
    return rules


def evaluate(
    rules: list[Rule],
    *,
    severity: Optional[str],
    event: Optional[str],
    counties_fips: list[str],
    alert_age_minutes: Optional[int],
) -> Optional[str]:
    """Return first matching rule's action or None.

    Actions: ENABLE, PAUSE, NOOP. Unknown action values are ignored.
    """
    for r in rules:
        if r.matches(
            severity=severity,
            event=event,
            counties_fips=counties_fips,
            alert_age_minutes=alert_age_minutes,
        ):
            act = r.action.upper()
            if act in ("ENABLE", "PAUSE", "NOOP"):
                return act
    return None
