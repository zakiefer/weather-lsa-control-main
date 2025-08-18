# Visual Baselines

This folder stores visual screenshot baselines for E2E tests.

Workflow:

- First run creates a baseline and marks the test xfail.
- Commit baselines if they look correct.
- Subsequent runs compare current screenshots to detect regressions.
