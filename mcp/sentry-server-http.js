#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";
import { randomUUID } from "node:crypto";

// Local-only HTTP MCP server for Sentry tools (stubbed, no cloud calls)
// Port can be overridden via env; defaults to 3999
const PORT = Number(process.env.SENTRY_HTTP_PORT || process.env.PORT || 3999);

function buildMcpServer() {
  const server = new McpServer({ name: "sentry-mcp-local", version: "0.1.0" });

  server.registerTool(
    "sentry_ping",
    { description: "Ping the Sentry MCP local server.", inputSchema: {} },
    async () => ({ content: [{ type: "text", text: "pong" }] })
  );

  server.registerTool(
    "sentry_info",
    {
      description: "Report local-only status and environment metadata.",
      inputSchema: { type: "object", properties: {} },
    },
    async () => {
      const info = {
        name: "sentry-mcp-local",
        version: "0.1.0",
        cloud_calls: false,
        local_only: true,
        pid: process.pid,
        port: PORT,
        node: process.version,
      };
      return { content: [{ type: "json", json: info }] };
    }
  );

  server.registerTool(
    "sentry_echo",
    {
      description: "Echo a small message (debug only).",
      inputSchema: {
        type: "object",
        properties: { message: { type: "string" } },
        required: ["message"],
      },
    },
    async ({ message }) => ({ content: [{ type: "text", text: String(message) }] })
  );

  return server;
}

// Shared config for local-only transport
function allowedHosts() {
  return [
    `localhost:${PORT}`,
    `127.0.0.1:${PORT}`,
    "localhost",
    "127.0.0.1",
  ];
}

// Session registries
const transports = new Map(); // sid -> transport
const servers = new Map(); // sid -> server

// App
const app = express();
app.use(express.json({ limit: "2mb" }));
app.use((req, res, next) => {
  res.setHeader("Access-Control-Expose-Headers", "mcp-session-id");
  next();
});

// Health
app.get("/health", (_req, res) => {
  res.status(200).json({ ok: true, local_only: true, name: "sentry-mcp-local" });
});

// POST /mcp — create/connect session on demand
app.post("/mcp", async (req, res) => {
  try {
    let t;
    const sid = req.headers["mcp-session-id"]; // optional for POST
    if (sid && transports.has(sid)) {
      t = transports.get(sid);
    } else {
      t = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        enableDnsRebindingProtection: true,
        allowedHosts: allowedHosts(),
        enableJsonResponse: true,
        onsessioninitialized: (newSid) => {
          transports.set(newSid, t);
          console.error(`[sentry-mcp-http] session started ${newSid}`);
        },
      });
      const server = buildMcpServer();
      await server.connect(t);
      // Note: server will be reachable once session is initialized
      servers.set("pending", server);
    }
    await t.handleRequest(req, res, req.body);
  } catch (e) {
    console.error("POST /mcp error", e);
    res
      .status(500)
      .json({ jsonrpc: "2.0", error: { code: -32000, message: "Internal error" }, id: null });
  }
});

// GET /mcp — requires valid session id
app.get("/mcp", async (req, res) => {
  try {
    const sid = req.headers["mcp-session-id"]; // required
    if (!sid || !transports.has(sid)) {
      res
        .status(400)
        .json({ jsonrpc: "2.0", error: { code: -32600, message: "Mcp-Session-Id header is required" }, id: null });
      return;
    }
    const t = transports.get(sid);
    await t.handleRequest(req, res);
  } catch (e) {
    console.error("GET /mcp error", e);
    res.status(500).end();
  }
});

// DELETE /mcp — end session
app.delete("/mcp", async (req, res) => {
  try {
    const sid = req.headers["mcp-session-id"]; // required
    if (!sid || !transports.has(sid)) {
      res
        .status(400)
        .json({ jsonrpc: "2.0", error: { code: -32600, message: "Mcp-Session-Id header is required" }, id: null });
      return;
    }
    const t = transports.get(sid);
    try {
      if (typeof t.close === "function") {
        await t.close();
      }
    } finally {
      transports.delete(sid);
    }
    res.status(204).end();
  } catch (e) {
    console.error("DELETE /mcp error", e);
    res.status(500).end();
  }
});

app.listen(PORT, () => {
  console.error(`[sentry-mcp-http] listening on http://127.0.0.1:${PORT}/mcp`);
});
