# Contributing

## Naming conventions (short guide)

Keep names simple, descriptive, and consistent. Favor clarity over brevity.

- Files and folders:
  - Python packages/modules: snake_case (e.g., `weather_monitor.py`, `lsa_client.py`).
  - Tests: `tests/test_*.py` mirroring modules under test.
  - Config/data dirs: lowercase, hyphen-less (e.g., `config/`, `secrets/`, `data/`, `logs/`).
- Classes: PascalCase (e.g., `WeatherMonitor`, `LSAClient`).
- Functions and methods: snake_case verbs (e.g., `check_severe_weather()`, `set_campaign_status()`).
- Variables and attributes: snake_case nouns (e.g., `storm_hold_time`, `base_url`).
- Constants and feature flags: UPPER_SNAKE (e.g., `VALIDATE_ONLY`, `ADS_QPS`).
- Tests:
  - Functions: `test_<behavior>_<condition>` (e.g., `test_quiet_hours_blocks_mutations`).
  - Fixtures: snake_case, avoid abbreviations (e.g., `temp_db_path`).
- Database schema:
  - Table names: snake_case singular or domain-grouped (e.g., `mutation_queue`, `region_mapping`).
  - Columns: snake_case with clear units/meaning (e.g., `created_at_utc`, `cooldown_until`).
- HTTP/API params and JSON keys:
  - Follow provider’s case when interacting with external APIs.
  - Internal models/dicts use snake_case.
- Ambiguity avoidance:
  - Prefer explicit units: `*_seconds`, `*_minutes`, `*_utc`, `max_distance_mi`.
  - Use intent-revealing names: `min_password_length` over `min_password`.
  - Boolean flags as predicates: `is_enabled`, `has_token`, `should_retry`.

## Additional guidance
- Avoid acronyms unless well-known (e.g., `NWS`, `LSA`), and expand at first use in docs.
- Keep function sizes small; extract helpers with purposeful names.
- Consistency beats “perfect” names—follow the existing style in this repo.
