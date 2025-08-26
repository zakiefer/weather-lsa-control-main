#!/usr/bin/env node
// Minimal Hugging Face MCP HTTP probe: list tools and call a simple one.
// Uses MCP Node SDK with Streamable HTTP transport (handles SSE without hanging).

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const HF_URL = process.env.HUGGINGFACE_MCP_URL || "http://127.0.0.1:3865/mcp";

function pickTool(tools) {
  const names = tools.map((t) => t.name);
  // Prefer a deterministic simple call
  if (names.includes("hf_sentiment")) return "hf_sentiment";
  if (names.includes("hf_search_models")) return "hf_search_models";
  if (names.includes("hf_whoami")) return "hf_whoami";
  // Fallback to any tool
  return names[0];
}

function toolNames(tools) {
  return tools.map((t) => t.name);
}

async function connectClient() {
  const url = new URL(HF_URL);
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  const transport = new StreamableHTTPClientTransport(url, {
    requestInit: { headers },
  });
  const client = new Client({ name: "hf-http-probe", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

async function main() {
  const client = await connectClient();
  try {
    const { tools } = await client.listTools();
    const names = toolNames(tools);
    console.log(JSON.stringify({ url: HF_URL }));
    console.log(JSON.stringify({ tools: names }));

    const chosen = pickTool(tools);
    // Always run initial sample call
    let args = {};
    if (chosen === "hf_sentiment") {
      args = { text: "I absolutely love this project!" };
    } else if (chosen === "hf_search_models") {
      args = { query: "bert", limit: 1 };
    }
    let result = await client.callTool({ name: chosen, arguments: args });
    console.log(JSON.stringify({ sample_call: { tool: chosen, args, result } }));

    // Smoke tests requested: batch sentiment and model override
    if (chosen === "hf_sentiment") {
      const batchArgs = { text: ["great job", "this is awful"] };
      const batchRes = await client.callTool({ name: chosen, arguments: batchArgs });
      console.log(JSON.stringify({ batch_call: { args: batchArgs, result: batchRes } }));

      const overrideArgs = {
        text: ["great job", "this is awful"],
        model_id: process.env.HF_SENT_OVERRIDE || "distilbert-base-uncased-finetuned-sst-2-english",
      };
      const overrideRes = await client.callTool({ name: chosen, arguments: overrideArgs });
      console.log(JSON.stringify({ override_call: { args: overrideArgs, result: overrideRes } }));
    }
  } finally {
    try { await client.close?.(); } catch {}
  }
}

main().catch((e) => {
  console.error("ERR:", e?.message || e);
  process.exit(1);
});
