#!/usr/bin/env bash
set -euo pipefail
PY=python3
"$PY" - <<'PYCODE'
import importlib, sys, subprocess, os
def have(m): 
    try: importlib.import_module(m); return True
    except: return False
pkgs=[("pytest","pytest")]
for mod,pkg in pkgs:
    if not have(mod):
        subprocess.check_call([sys.executable,"-m","pip","install","--user",pkg])
PYCODE
