# MCP progress notifications

This repo includes a minimal MCP server that emits progress updates during long operations so Copilot Chat can show incremental percentages.

## Included demo server

- File: `tools/mcp_progress.py`
- Server id: `progress-demo`
- Start mode: stdio
- Tool: `long_task(seconds=10, steps=10, label="Working…", progress_token=None)`

The tool emits progress from 0% to 100% over the chosen `seconds`, splitting into `steps` updates. Copilot Chat will display these as a progress bar.

## How it works (Python SDK)

Using the official `mcp` Python SDK:

- Create a server with `FastMCP("progress-demo")`.
- Inside your async tool, call `await server.progress(pct, label=..., progress_token=...)` where `pct` is `0.0..1.0`.
- Accept an optional `progress_token` argument and pass it back when reporting progress.

Example snippet inside a tool:

```python
@tool()
async def long_task(seconds: int = 10, steps: int = 10, progress_token: str | None = None):
    await server.progress(0.0, label="Working…", progress_token=progress_token)
    interval = seconds / max(1, steps)
    for i in range(1, steps + 1):
        await anyio.sleep(interval)
        await server.progress(i / steps, label="Working…", progress_token=progress_token)
    return {"status": "ok"}
```

## Configuration

The server is wired into `.mcp.json`:

```json
{
  "servers": {
    "progress-demo": { "command": "python3 tools/mcp_progress.py" }
  }
}
```

No API keys are needed. Ensure your environment has `mcp` installed in the active Python:

```sh
pip install mcp anyio
```

## Verifying in Copilot Chat

- Trigger the `progress-demo` server and run the `long_task` tool with a longer duration, e.g., `seconds=20` and `steps=20`.
- You should see progress updates appear incrementally (0% → 100%).

## Adopting in your own servers

- If you maintain custom MCP servers, accept a `progress_token` in long-running tools and call the SDK’s `progress()` periodically.
- Keep updates under a reasonable cadence (e.g., 10–50 updates) to avoid UI spam.
- Always send an initial 0% and a final 100% update.
