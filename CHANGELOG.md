# Changelog

All notable changes to this project will be documented in this file.

## [0.1.1] - 2025-08-13
### Added
- CLI toggles for region mappings: `--use-region-mappings` and `--no-region-mappings`.
- Environment toggle `USE_REGION_MAPPINGS` (default enabled). Set to `false` to ignore DB mappings and use default IDs.
- README examples and deploy docs for mapping toggles.

### Changed
- Default ENABLE/PAUSE actions (when no mappings) now use env IDs to avoid stale settings in tests.
- Rules-driven `PAUSE` bypasses cooldown suppression so intentional rule actions are applied.

### Fixed
- worker.py import-time SyntaxError by correcting try/rollback structure.
- Golden tests aligned with Ads GAQL/mutate payloads and validateOnly gate.

## [0.1.0] - 2025-08-13
### Initial release
- Weather-based LSA controller with SQLite state, queue worker, rate-limiting, circuit breaker, and health/metrics.
- Rules engine (YAML/JSON) and safety features (validate-only gate, LSA-only guard, labels).
