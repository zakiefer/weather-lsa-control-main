# SLint MCP setup (stdio)

Status: package not found on npm as of this setup.

- We looked for an npm package named `slint-mcp`, `slint-mcp-server`, and `@slint/mcp` and found none.
- Until an official package exists, we keep `.mcp/servers.conf` unchanged and add a commented stub in `.vscode/mcp.json`.

## Workspace wiring

- `.vscode/mcp.json` includes a commented entry for `slint` using `npx -y <slint-mcp-package-or-exec>`.
- When an official server is published, replace `<slint-mcp-package-or-exec>` with the real command and remove comments.

Example:

```jsonc
{
  "servers": {
    "slint": {
      "command": "npx",
      "args": ["-y", "slint-mcp"],
      "transport": "stdio",
      "autoStart": true
    }
  }
}
```

## Status visibility

- `status-mcp.sh` reads `.mcp/servers.conf` by default.
- To include `.vscode/mcp.json` servers (like `slint`) run:
  - INCLUDE_VSCODE_MCP_JSON=1 ./scripts/mcp/status-mcp.sh

## Verify when available

1) Reload window (we have a surrogate `vscode:reload` task).
2) Run the default build task “MCP: Start all + Notify”.
3) Check status with inclusion flag (above).
4) Use `tools/mcp_stdio_probe.py --command <slint-exec> --list` to list tools, then try a simple tool.

## Notes

- We removed an invalid `slint-mcp` devDependency to keep `package.json` valid.
- If SLint ships a binary or Python server instead, adapt the `command` and `args` accordingly.
