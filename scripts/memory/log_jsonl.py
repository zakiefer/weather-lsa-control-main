#!/usr/bin/env python3
import json
import os
import sys
import time
from datetime import datetime, timezone

LOG_DIR = "ops/logs"
LOG_FILE = os.path.join(LOG_DIR, "agent_actions.jsonl")
MAX_BYTES = 5 * 1024 * 1024  # 5MB


def atomic_append(path: str, line: str) -> None:
    tmp = path + ".tmp." + str(time.time_ns())
    with open(tmp, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
    os.replace(tmp, path)


def rotate_if_needed(path: str) -> None:
    if os.path.exists(path) and os.path.getsize(path) >= MAX_BYTES:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        os.rename(path, path + f".{ts}.rotated")


def ensure_ts(obj: dict) -> None:
    if not obj.get("ts"):
        obj["ts"] = datetime.now(timezone.utc).isoformat()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: log_jsonl.py '{\"goal\":\"...\"}'", file=sys.stderr)
        sys.exit(2)
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        obj = json.loads(sys.argv[1])
    except Exception as e:  # noqa: BLE001 - broad to ensure robust CLI handling
        print(f"[log_jsonl] invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    ensure_ts(obj)
    rotate_if_needed(LOG_FILE)
    atomic_append(LOG_FILE, json.dumps(obj, ensure_ascii=False))
    print("[log_jsonl] ok")


if __name__ == "__main__":
    main()
