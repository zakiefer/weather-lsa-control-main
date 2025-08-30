#!/usr/bin/env node
// Minimal Apify MCP HTTP probe: list tools and call a simple one.
// Requires: @modelcontextprotocol/sdk (already in package.json)

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const APIFY_URL = process.env.APIFY_MCP_URL || "https://mcp.apify.com";
const APIFY_TOKEN = process.env.APIFY_TOKEN || "";

function pickSimpleTool(tools) {
  const names = tools.map((t) => t.name);
  // Prefer list/ping/info tools
  const prefer = names.find((n) => /\b(list|ping|info|whoami|status)\b/i.test(n));
  return prefer || names[0];
}

function toolNames(tools) {
  return tools.map((t) => t.name);
}

async function connectClient() {
  const url = new URL(APIFY_URL);
  const transport = new StreamableHTTPClientTransport(url, {
    authProvider: {
      tokens: async () => ({ access_token: APIFY_TOKEN }),
    },
    requestInit: {
      // Ensure we send JSON and accept SSE where appropriate
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
      },
    },
  });
  const client = new Client({ name: "apify-http-probe", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

async function main() {
  if (!APIFY_TOKEN) {
    console.error("ERR: APIFY_TOKEN not set in environment");
    process.exit(2);
  }
  const client = await connectClient();
  try {
    const { tools } = await client.listTools();
    const names = toolNames(tools);
    // Print requested outputs
    console.log(JSON.stringify({ apify_token_present: true }));
    console.log(JSON.stringify({ tools: names }));

    const chosen = pickSimpleTool(tools);
    let result;
    try {
      // Try empty payload first
      result = await client.callTool({ name: chosen, arguments: {} });
    } catch (e) {
      // Provide tiny defaults for a few known tools
      const minimalArgs = (() => {
        if (/get-actor-details/i.test(chosen)) return { actor: "apify/hello-world" };
        if (/search-actors/i.test(chosen)) return { query: "hello world", limit: 1 };
        if (/search-apify-docs/i.test(chosen)) return { query: "actor", limit: 1 };
        return { limit: 1 };
      })();
      result = await client
        .callTool({ name: chosen, arguments: minimalArgs })
        .catch(() => ({ error: "call_failed", tool: chosen, message: String(e?.message || e) }));
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
