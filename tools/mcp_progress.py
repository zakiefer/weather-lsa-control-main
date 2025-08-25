#!/usr/bin/env python3
"""
Minimal MCP server that demonstrates progress notifications.

It exposes a single tool, "demo.long_task", that simulates a long-running
operation and emits progress updates from 0 → 100% so Copilot Chat can
surface incremental status.

Requirements:
  pip install mcp

Run (stdio):
  python tools/mcp_progress.py
"""

from __future__ import annotations

import sys
from typing import Any
from uuid import uuid4


def main() -> int:
    try:
        # The official Python SDK for the Model Context Protocol
        import anyio  # type: ignore  # noqa: I001
        from mcp.server.fastmcp import FastMCP  # type: ignore  # noqa: I001
    except Exception as e:
        sys.stderr.write(
            "ERROR: This demo requires the 'mcp' package (and anyio).\n"
            "Install for dev use: pip install mcp anyio\n"
            f"Details: {e}\n"
        )
        return 2

    # Instantiate server
    server: Any = FastMCP("progress-demo")
    cancelled: set[str] = set()

    @server.tool(description="Simulate a long task and emit progress updates (0→100%).")
    async def long_task(
        context: Any,
        seconds: int = 10,
        steps: int = 10,
        label: str = "Working…",
        progress_token: str | None = None,
    ) -> dict[str, Any]:
        """Simulate work and emit progress notifications."""
        steps = max(1, int(steps))
        seconds = max(0, int(seconds))
        token = progress_token or str(uuid4())

        # Emit an initial 0% update
        await context.report_progress(0.0, label=label, progress_token=token)  # type: ignore[attr-defined]

        # Spread updates evenly across the total duration
        interval = (seconds / steps) if steps else 0
        for i in range(1, steps + 1):
            # Sleep between updates to simulate work
            if interval > 0:
                await anyio.sleep(interval)
            if token in cancelled:
                await context.report_progress(1.0, label="Cancelled", progress_token=token)  # type: ignore[attr-defined]
                cancelled.discard(token)
                return {
                    "status": "cancelled",
                    "seconds": seconds,
                    "steps": i - 1,
                    "label": label,
                    "progress_token": token,
                }
            pct = i / steps
            await context.report_progress(pct, label=label, progress_token=token)  # type: ignore[attr-defined]

        return {
            "status": "ok",
            "seconds": seconds,
            "steps": steps,
            "label": label,
            "progress_token": token,
        }

    @server.tool(description="Request cancellation for a running long_task using its progress_token.")
    async def cancel_task(progress_token: str) -> dict[str, Any]:
        cancelled.add(progress_token)
        return {"status": "ok", "cancelled": True, "progress_token": progress_token}

    # Start stdio JSON-RPC server (async)
    async def _run() -> int:
        await server.run_stdio_async()
        return 0

    return anyio.run(_run)


if __name__ == "__main__":
    raise SystemExit(main())
