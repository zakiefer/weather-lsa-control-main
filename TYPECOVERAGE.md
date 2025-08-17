# Type coverage report (baseline)

Date: 2025-08-16

Goal: Raise public API type coverage to >= 70%.

How to measure (suggested):

- Use mypy (already configured) and Pyright (pyrightconfig.json present) in CI.
- Manual snapshot: run `pyright --stats` locally to gauge coverage.

Suggested focus files and functions:

- src/lsa_client.py: add return types and narrow parameter types for mutate and fetch methods.
- src/rules.py: annotate rule predicates and evaluation helpers.
- src/db.py: annotate connection and row types; surface TypedDicts for rows.

Next steps checklist:

- [ ] Add `src/py.typed` marker file to mark the package as typed.
- [ ] Annotate public functions in the three focus files.
- [ ] Enable Pyright in CI (non-blocking) and attach stats as an artifact.
- [ ] Track improvements here with date-stamped deltas.
