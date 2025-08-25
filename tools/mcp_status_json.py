#!/usr/bin/env python3
"""
Emit MCP status as JSON by parsing the tabular output of scripts/mcp/status-mcp.sh.

This wrapper sets INCLUDE_VSCODE_MCP_JSON=1 to include servers from .vscode/mcp.json
per the workspace conventions, then parses the fixed-width columns printed by the
status script ("%-18s %-8s %-8s %-6s %s").

Output schema:
{
  "model": "gpt-5",
  "rows": [
    {"name": "llm-router", "pid": "12345", "alive": true, "port": "", "log": ".mcp/logs/llm-router.log"},
    ...
  ]
}
"""

from __future__ import annotations

import json
import os
import subprocess


def parse_status_lines(lines: list[str]) -> dict[str, object]:
    rows = []
    model = None
    # Skip header if present
    if lines and lines[0].strip().startswith("NAME"):
        lines = lines[1:]
    for line in lines:
        s = line.rstrip("\n")
        if not s:
            continue
        if s.startswith("Effective OPENAI_MODEL="):
            model = s.split("=", 1)[-1].strip()
            break
        # Ensure the line has enough width to slice; pad with spaces
        if len(s) < 45:
            s = s + (" " * (45 - len(s)))
        # Fixed-width columns according to printf format in status-mcp.sh
        name = s[0:18].strip()
        pid = s[19:27].strip()
        alive = s[28:36].strip()
        port = s[37:43].strip()
        log = s[44:].strip()
        if not name and not log:
            continue
        rows.append(
            {
                "name": name,
                "pid": pid,
                "alive": alive.lower() == "yes",
                "port": port,
                "log": log,
            }
        )
    return {"model": model, "rows": rows}


def main() -> int:
    env = os.environ.copy()
    env["INCLUDE_VSCODE_MCP_JSON"] = "1"
    try:
        proc = subprocess.run(
            ["./scripts/mcp/status-mcp.sh"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print(
            json.dumps(
                {
                    "error": "status-failed",
                    "returncode": e.returncode,
                    "stdout": e.stdout,
                    "stderr": e.stderr,
                }
            )
        )
        return 1

    data = parse_status_lines(proc.stdout.splitlines())
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
