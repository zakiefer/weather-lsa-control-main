# MCP Progress (VS Code Extension)

Shows MCP progress as a cancellable notification and a status bar percent.

- Connects to the local demo server `tools/mcp_progress.py` via stdio.
- Listens for `notifications/progress` messages.
- Updates a Status Bar item and a cancellable progress notification.

Commands:

- MCP: Connect to Progress Demo
- MCP: Start Long Task (Progress Demo)

Build:

```sh
cd extensions/mcp-progress
npm install
npm run compile
```

Run: press F5 to launch the Extension Development Host.
