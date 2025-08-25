#!/usr/bin/env python3
"""
Generic MCP stdio verification helper.

- Connects to a stdio MCP server command
- Lists tools
- Optionally calls a specified tool with JSON args

Examples:
    .venv/bin/python tools/mcp_verify_stdio.py \
        --command /Users/program/.local/bin/arxiv-mcp-server --list
    .venv/bin/python tools/mcp_verify_stdio.py \
        --command /Users/program/.local/bin/arxiv-mcp-server \
        --call search '{"query":"test","max_results":1}'
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import anyio

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    sys.stderr.write("ERROR: 'mcp' package not available in this environment.\n")
    raise


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--command", required=True, help="MCP stdio server command path")
    ap.add_argument("--list", action="store_true", help="List tools and print JSON")
    ap.add_argument("--call", dest="tool", help="Tool name to call (optional)")
    ap.add_argument("args", nargs="?", help="JSON string of arguments for the tool call (optional)")
    return ap.parse_args()


async def _verify(args: argparse.Namespace) -> dict[str, Any]:
    params = StdioServerParameters(command=args.command, args=[])
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            out: dict[str, Any] = {}
            # tools list
            tools = await session.list_tools()
            out["tools"] = [t.name for t in tools.tools]
            # optional call
            if args.tool:
                payload = {}
                if args.args:
                    try:
                        payload = json.loads(args.args)
                    except Exception as e:  # pragma: no cover
                        out["call_error"] = {"type": "invalid-args", "details": str(e)}
                        return out
                try:
                    result = await session.call_tool(args.tool, arguments=payload)
                    out["call"] = {
                        "tool": args.tool,
                        "args": payload,
                        "result": result.model_dump(mode="json"),
                    }
                except Exception as e:  # pragma: no cover
                    out["call_error"] = {"type": "call-failed", "tool": args.tool, "details": str(e)}
            return out


async def main() -> int:
    ns = _parse_args()
    try:
        data = await _verify(ns)
        print(json.dumps(data, indent=2))
        return 0
    except FileNotFoundError as e:
        print(json.dumps({"error": "command-not-found", "command": ns.command, "details": str(e)}))
        return 127
    except Exception as e:  # pragma: no cover
        print(json.dumps({"error": "connect-failed", "command": ns.command, "details": str(e)}))
        return 1


if __name__ == "__main__":
    try:
        code = anyio.run(main)
    except LookupError:
        code = anyio.run(main)
    raise SystemExit(code)
