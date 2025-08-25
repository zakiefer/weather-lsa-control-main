#!/usr/bin/env python3
"""
Probe the Clarity MCP server via stdio using the Python MCP client.

- Resolves the server command in this order:
    1) env CLARITY_MCP_CMD (may include args)
    2) `clarity-mcp` found on PATH
    3) Node dist at ./dist/index.js (uses `node dist/index.js`)
    4) Python module (`python -m clarity_mcp.server`)

- Passes through env CLARITY_PROJECT_ID and CLARITY_API_TOKEN
- Initializes client session, lists tools, heuristically picks a metrics tool,
    executes a call with a representative payload, and prints JSON on success.
- Captures server stderr to logs/mcp/clarity.probe.err.log for diagnostics.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
from datetime import timedelta
from pathlib import Path
from shutil import which

import anyio

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    sys.stderr.write("ERROR: This probe requires the 'mcp' Python package to be installed in your venv.\n\n")
    raise


PAYLOAD = {
    "projectId": "szifqljfx9",
    "metric": "Traffic",
    "dimensions": ["Browser", "Device"],
    "from": "2025-08-15",
    "to": "2025-08-23",
}


def detect_command_with_args(root: Path) -> tuple[str, list[str], str]:
    """
    Detect the Clarity MCP server command and args.

    Returns: (command, args, reason)
    """
    # 1) Explicit env override
    env_cmd = os.environ.get("CLARITY_MCP_CMD")
    if env_cmd:
        parts = shlex.split(env_cmd)
        if not parts:
            raise RuntimeError("CLARITY_MCP_CMD is set but empty after parsing")
        return parts[0], parts[1:], "env:CLARITY_MCP_CMD"

    # 2) PATH binary
    path_cmd = which("clarity-mcp")
    if path_cmd:
        return path_cmd, [], "PATH:clarity-mcp"

    # 3) Local Node dist
    dist = root / "dist" / "index.js"
    if dist.exists():
        return "node", [str(dist)], "node:dist/index.js"

    # 4) Python module
    try:
        import importlib.util

        if importlib.util.find_spec("clarity_mcp") is not None:
            py = sys.executable or "python3"
            return py, ["-m", "clarity_mcp.server"], "python:-m clarity_mcp.server"
    except Exception:
        pass

    # Fallback: likely missing; stderr will capture details
    return "/usr/local/bin/clarity-mcp", [], "fallback:/usr/local/bin/clarity-mcp"


async def run_probe() -> int:
    root = Path(__file__).resolve().parents[1]
    cmd, args, reason = detect_command_with_args(root)
    env_pairs: list[tuple[str, str]] = []
    for key in ("CLARITY_PROJECT_ID", "CLARITY_API_TOKEN"):
        val = os.environ.get(key)
        if not val:
            sys.stderr.write(f"Missing required env {key}. Set it and re-run.\n")
            return 2
        env_pairs.append((key, val))

    # Prepare stderr capture via a tiny wrapper to a log file.
    log_dir = root / "logs" / "mcp"
    log_dir.mkdir(parents=True, exist_ok=True)
    err_log = log_dir / "clarity.probe.err.log"
    wrapper = log_dir / "clarity-mcp-wrapper.sh"

    # Create a wrapper that invokes the detected command and args explicitly,
    # ensuring stderr is appended to our log file.
    # We quote each arg defensively to preserve spaces.
    def q(s: str) -> str:
        return shlex.quote(s)

    full_cmd = " ".join([q(cmd), *[q(a) for a in args]])
    wrapper.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
exec {full_cmd} "$@" 2>>"{err_log}"
""",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Connect to the stdio server via wrapper (stderr captured to err_log)
    params = StdioServerParameters(command=str(wrapper), args=[], env=dict(env_pairs))
    try:
        sys.stderr.write(f"Resolved command: {cmd} {' '.join(args)} [{reason}]\n")
        sys.stderr.write(f"Stderr log: {err_log}\n")
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # Initialize and list tools
                await session.initialize()
                tools = await session.list_tools()
                # Heuristic: find a tool that looks like a metrics fetch
                tool_name = None
                for t in tools.tools:
                    name = t.name.lower()
                    if any(k in name for k in ("clarity", "metric", "analytics", "fetch", "report")):
                        tool_name = t.name
                        break
                if tool_name is None and tools.tools:
                    tool_name = tools.tools[0].name

                if tool_name is None:
                    sys.stderr.write("No tools exposed by Clarity MCP server.\n")
                    # Print any stderr we captured for debugging
                    if err_log.exists():
                        sys.stderr.write("\nCaptured server stderr:\n")
                        sys.stderr.write(err_log.read_text()[-4000:])
                    return 3

                # Call tool with payload
                result = await session.call_tool(
                    tool_name,
                    arguments=PAYLOAD,  # type: ignore[arg-type]
                    read_timeout_seconds=timedelta(seconds=30),
                )

                # Print pretty JSON result
                print(json.dumps(result.model_dump(mode="json"), indent=2))
                return 0
    except Exception as e:
        sys.stderr.write(f"Probe failed: {e}\n")
        # Print any stderr we captured for debugging
        if err_log.exists():
            sys.stderr.write("\nCaptured server stderr:\n")
            try:
                sys.stderr.write(err_log.read_text()[-4000:])
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    # Prefer trio backend if available; otherwise fall back to default anyio backend
    try:
        exit_code = anyio.run(run_probe, backend="trio")
    except LookupError:
        exit_code = anyio.run(run_probe)  # default backend
    raise SystemExit(exit_code)
