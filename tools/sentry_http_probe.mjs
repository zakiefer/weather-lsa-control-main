#!/usr/bin/env node
// Minimal Sentry MCP HTTP probe: list tools and call a simple one.
// Requires: @modelcontextprotocol/sdk (already in package.json)

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import fs from "fs";
import path from "path";

// Enforce local-only by default: require explicit SENTRY_MCP_URL or default to localhost.
const SENTRY_URL = process.env.SENTRY_MCP_URL || "http://127.0.0.1:3999/mcp";
let SENTRY_AUTH_TOKEN = process.env.SENTRY_AUTH_TOKEN || "";
let SENTRY_ORG_SLUG = process.env.SENTRY_ORG_SLUG || "";

function getDotEnvValue(key) {
  try {
    const envPath = path.resolve(process.cwd(), ".env.local");
    if (!fs.existsSync(envPath)) return "";
    const content = fs.readFileSync(envPath, "utf8");
    for (const line of content.split(/\r?\n/)) {
      if (!line || /^\s*#/.test(line)) continue;
  const m = line.match(new RegExp(`^\\s*${key}\\s*=\\s*(.*)\\s*$`));
      if (m) {
        let v = m[1];
        if ((v.startsWith("\"") && v.endsWith("\"")) || (v.startsWith("'") && v.endsWith("'"))) {
          v = v.slice(1, -1);
        }
        return v.trim();
      }
    }
  } catch {}
  return "";
}

function pickSimpleTool(tools) {
  const names = tools.map((t) => t.name);
  // Prefer list/info/ping tools
  const prefer = names.find((n) => /\b(list|ping|info|whoami|status)\b/i.test(n));
  return prefer || names[0];
}

function toolNames(tools) {
  return tools.map((t) => t.name);
}

async function connectClient() {
  const url = new URL(SENTRY_URL);
  const isLocal = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  if (SENTRY_AUTH_TOKEN) {
    headers["Authorization"] = `Bearer ${SENTRY_AUTH_TOKEN}`;
  }
  if (SENTRY_ORG_SLUG && !isLocal) {
    headers["Sentry-Organization"] = SENTRY_ORG_SLUG;
  }
  // Print redacted header debug for diagnostics
  try {
    const dbg = {
      url: url.toString(),
      headers: {
        ...(SENTRY_AUTH_TOKEN ? { Authorization: "Bearer ****REDACTED****" } : {}),
        ...(SENTRY_ORG_SLUG ? { "Sentry-Organization": SENTRY_ORG_SLUG } : {}),
        "content-type": headers["content-type"],
        accept: headers.accept,
      },
    };
    console.log(JSON.stringify({ debug_http: dbg }));
  } catch {}
  const transport = new StreamableHTTPClientTransport(url, {
    requestInit: { headers },
  });
  const client = new Client({ name: "sentry-http-probe", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

async function main() {
  const url = new URL(SENTRY_URL);
  const isLocal = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  if (!isLocal) {
    // Only require token for non-local endpoints
    if (!SENTRY_AUTH_TOKEN) {
      const fallback = getDotEnvValue("SENTRY_AUTH_TOKEN");
      if (fallback) {
        SENTRY_AUTH_TOKEN = fallback;
      } else {
        console.error("ERR: SENTRY_AUTH_TOKEN not set (env or .env.local) for non-local Sentry MCP URL");
        process.exit(2);
      }
    }
    if (!SENTRY_ORG_SLUG) {
      const orgFallback = getDotEnvValue("SENTRY_ORG_SLUG");
      if (orgFallback) {
        SENTRY_ORG_SLUG = orgFallback;
      }
    }
  }
  const client = await connectClient();
  try {
    const { tools } = await client.listTools();
    const names = toolNames(tools);
    console.log(JSON.stringify({ sentry_token_present: true }));
    console.log(JSON.stringify({ tools: names }));
    const chosen = pickSimpleTool(tools);
    let result;
    try {
      result = await client.callTool({ name: chosen, arguments: {} });
    } catch (e) {
      const minimalArgs = (() => {
        if (/search|list/i.test(chosen)) return { query: "health", limit: 1 };
        return {};
      })();
      try {
        result = await client.callTool({ name: chosen, arguments: minimalArgs });
      } catch (e2) {
        result = { error: "call_failed", tool: chosen, message: String(e2?.message || e2) };
      }
    }
    console.log(JSON.stringify({ sample_call: { tool: chosen, result } }));
  } finally {
    try { await client.close?.(); } catch {}
  }
}

main().catch((e) => {
  console.error("ERR:", e?.message || e);
  process.exit(1);
});
