# Secret scanning and rotation guide

This repository contains a `secrets/` folder for local development. To avoid accidental leaks:

## Setup

- Install pre-commit and hooks: `make install-dev && make pre-commit-install`
- Add secret scanning hook (detect-secrets) in `.pre-commit-config.yaml`.
- Generate a baseline: `detect-secrets scan > .secrets.baseline`
- Commit the baseline.

## Workflow

- Before committing, pre-commit will block if secrets are detected.
- If a real secret was committed, rotate it immediately and purge history if needed.

## Rotation checklist

- [ ] Revoke the leaked credential (API key/token/password).
- [ ] Generate a new credential.
- [ ] Update the secret in your secret manager and `.env` if applicable.
- [ ] Invalidate any long-lived sessions.
- [ ] Add tests to prevent reliance on hardcoded secrets.
