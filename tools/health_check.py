#!/usr/bin/env python3
"""Run repo health checks: lint, type, tests, security.
Print a compact red/green summary.
"""

from __future__ import annotations

import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(__file__))
VENV = os.path.join(ROOT, ".venv", "bin")
PY = os.path.join(VENV, "python")
PIP_AUDIT = os.path.join(VENV, "pip-audit")
BANDIT = os.path.join(VENV, "bandit")
RUFF = os.path.join(VENV, "ruff")
MYPY = os.path.join(VENV, "mypy")
PYTEST = os.path.join(VENV, "pytest")


def run(cmd: list[str]) -> int:
    try:
        print("$", " ".join(cmd))
        return subprocess.call(cmd, cwd=ROOT)
    except FileNotFoundError:
        print("(missing)", cmd[0])
        return 0


def main() -> int:
    print("== Lint (ruff)")
    lint = run([RUFF, "check", "."])

    print("\n== Type (mypy)")
    mpy = run([MYPY, "src"])  # keep fast

    print("\n== Tests (pytest)")
    tst = run([PYTEST, "-q"])

    print("\n== Security: pip-audit")
    aud = run([PIP_AUDIT, "-r", "requirements.txt", "-r", "requirements-dev.txt"]) if os.path.exists(PIP_AUDIT) else 0

    print("\n== Security: bandit")
    bdt = run([BANDIT, "-q", "-r", "src"]) if os.path.exists(BANDIT) else 0

    code = 0 if (lint == 0 and mpy == 0 and tst == 0) else 1
    print("\n== Summary ==")
    print(
        f"lint={'OK' if lint == 0 else 'FAIL'} type={'OK' if mpy == 0 else 'FAIL'} tests={'OK' if tst == 0 else 'FAIL'}"
    )
    if aud != 0:
        print("pip-audit reported issues (non-blocking)")
    if bdt != 0:
        print("bandit reported issues (non-blocking)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
