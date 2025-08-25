#!/usr/bin/env python3
"""
Generic MCP stdio probe utility.

- Connects to a stdio MCP server executable and lists tools (JSON)
- Optionally calls the arxiv search tool with requested args and fallbacks

Notes:
- Observed arxiv tool names include: `search_papers`, `download_paper`, `list_papers`, `read_paper`.
- Older servers may expose `arxiv.search` or `search`.
- Argument variants tried: `max_results`, `limit`, `maxResults`.

Usage examples:
    .venv/bin/python tools/mcp_stdio_probe.py --command /Users/program/.local/bin/arxiv-mcp-server --list
    .venv/bin/python tools/mcp_stdio_probe.py --command /Users/program/.local/bin/arxiv-mcp-server --search
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

import anyio

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    sys.stderr.write("ERROR: 'mcp' package not available in this environment.\n")
    raise


async def do_list(session: ClientSession) -> dict:
    tools = await session.list_tools()
    # Convert to plain JSON-serializable dict
    return tools.model_dump(mode="json")


async def do_search(session: ClientSession) -> dict:
    # Discover the search tool name: prefer 'search_papers', then 'arxiv.search', 'search', else first matching 'arxiv'
    tools = await session.list_tools()
    names = [t.name for t in tools.tools]
    preferred = None
    if "search_papers" in names:
        preferred = "search_papers"
    elif "arxiv.search" in names:
        preferred = "arxiv.search"
    elif "search" in names:
        preferred = "search"
    else:
        preferred = next((n for n in names if "arxiv" in n.lower()), names[0] if names else None)
    if not preferred:
        return {"error": "no-tools", "details": names}

    # Try argument variants per instructions
    query = "transformer neural networks"
    attempts = [
        {"query": query, "max_results": 2},
        {"query": query, "limit": 2},
        {"query": query, "maxResults": 2},
    ]
    last_err: str | None = None
    for args in attempts:
        try:
            result = await session.call_tool(preferred, arguments=args, read_timeout_seconds=timedelta(seconds=45))
            return {
                "tool": preferred,
                "args": args,
                "result": result.model_dump(mode="json"),
            }
        except Exception as e:  # pragma: no cover
            last_err = str(e)
            continue
    return {"error": "call-failed", "tool": preferred, "last_error": last_err}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--command",
        default="/Users/program/.local/bin/arxiv-mcp-server",
        help="MCP stdio server command path",
    )
    ap.add_argument("--list", action="store_true", help="List tools and print JSON")
    ap.add_argument("--search", action="store_true", help="Call arxiv search tool with fallbacks and print JSON")
    args = ap.parse_args()

    params = StdioServerParameters(command=args.command, args=[])
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                out: dict[str, object] = {}
                if args.list:
                    out["tools_list"] = await do_list(session)
                if args.search:
                    out["search"] = await do_search(session)
                if not out:
                    out["note"] = "no action specified; use --list and/or --search"
                print(json.dumps(out, indent=2))
        return 0
    except FileNotFoundError as e:
        print(json.dumps({"error": "command-not-found", "command": args.command, "details": str(e)}), flush=True)
        return 127
    except Exception as e:  # pragma: no cover
        print(json.dumps({"error": "connect-failed", "command": args.command, "details": str(e)}), flush=True)
        return 1


if __name__ == "__main__":
    try:
        code = anyio.run(main)
    except LookupError:
        # Fallback to default backend
        code = anyio.run(main)
    raise SystemExit(code)
