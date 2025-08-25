# MCP server enablement — persistent workflow

Use this flow whenever enabling a new MCP server in this workspace.

- Transport: stdio
- Auto start: true
- Preserve existing `.vscode/mcp.json` and keep JSON valid
- Verification is automated via status + stdio probe

## Steps

1. Edit or create `.vscode/mcp.json` and add your server entry:

- NPM (via npx):

```jsonc
"<SERVER>": {
  "command": "npx",
  "args": ["-y", "<NPM_PACKAGE_OR_EXEC>"],
  "transport": "stdio",
  "autoStart": true
}
```

- Local executable:

```jsonc
"<SERVER>": {
  "command": "<ABS_OR_REL_PATH_TO_EXE>",
  "transport": "stdio",
  "autoStart": true
}
```

1. Reload and restart MCP

- Run task: “vscode:reload”
- Run task: “MCP: Restart all”

1. Verify

- Include VS Code servers in status:

```sh
INCLUDE_VSCODE_MCP_JSON=1 ./scripts/mcp/status-mcp.sh
```
- Probe stdio server (tools/list, then a sample call):

```sh
.venv/bin/python tools/mcp_stdio_probe.py --command <server-exec> --list
.venv/bin/python tools/mcp_stdio_probe.py --command <server-exec> --search # or server-specific sample
```

1. If the package/executable name is wrong

- Look it up in the registry/readme, correct `.vscode/mcp.json`, and repeat step 3.

1. Persist the action

- Append a brief note to `docs/MCP_<SERVER>.md` with:
  - Exact command used (or npm package@version)
  - The status row and `tools/list` excerpt
  - Raw JSON from the sample tool call

## Notes

- `status-mcp.sh` reads `.mcp/servers.conf` by default. To see VS Code-declared servers (like newly enabled ones), export `INCLUDE_VSCODE_MCP_JSON=1`.
- Keep `.mcp/servers.conf` authoritative for default fleet-noise control; use `.vscode/mcp.json` to add workspace-local servers.

### Arxiv MCP tool names

- Observed tools include: `search_papers`, `download_paper`, `list_papers`, `read_paper`.
- Use these names directly with `tool <name> { ... }`. Example:

```text
tool search_papers { "query": "transformer neural networks", "max_results": 2 }
```
