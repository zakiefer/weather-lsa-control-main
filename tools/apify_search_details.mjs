#!/usr/bin/env node
// Calls Apify MCP tools:
// 1) search-actors { query: "hello world", limit: 3 }
// 2) get-actor-details { actorId: <first result id> }
// Prints a single JSON line with { name, id, description }

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const APIFY_URL = process.env.APIFY_MCP_URL || "https://mcp.apify.com";
const APIFY_TOKEN = process.env.APIFY_TOKEN || "";

function extractJsonFromContent(result) {
  // Expects result like { content: [ { type: 'json', json: ... } | { type: 'text', text: '...' } ] }
  try {
    const items = result?.content ?? [];
    for (const c of items) {
      if (c?.type === "json" && c.json) return c.json;
    }
    // If only text, try to parse JSON from text
    for (const c of items) {
      if (c?.type === "text" && typeof c.text === "string") {
        const m = c.text.trim();
        try { return JSON.parse(m); } catch {}
      }
    }
    return null;
  } catch {
    return null;
  }
}

function extractTextFromContent(result) {
  const items = result?.content ?? [];
  const t = items.find((c) => c?.type === "text")?.text;
  return typeof t === "string" ? t : null;
}

async function connectClient() {
  const url = new URL(APIFY_URL);
  const transport = new StreamableHTTPClientTransport(url, {
    authProvider: { tokens: async () => ({ access_token: APIFY_TOKEN }) },
    requestInit: {
      headers: { "content-type": "application/json", accept: "application/json, text/event-stream" },
    },
  });
  const client = new Client({ name: "apify-search-details", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

function takeFirstActorId(searchJson, fallbackText) {
  // Try common shapes
  if (!searchJson) return null;
  // Shape 1: { items: [ { id, name, ... } ] }
  if (Array.isArray(searchJson.items) && searchJson.items.length > 0) {
    return searchJson.items[0].id || searchJson.items[0].actorId || null;
  }
  // Shape 2: [ { id, ... } ]
  if (Array.isArray(searchJson) && searchJson.length > 0) {
    return searchJson[0].id || searchJson[0].actorId || null;
  }
  // Try deep search for first object with id
  try {
    const stack = [searchJson];
    while (stack.length) {
      const cur = stack.pop();
      if (cur && typeof cur === "object") {
        if (cur.id || cur.actorId) return cur.id || cur.actorId;
        for (const v of Object.values(cur)) {
          if (v && typeof v === "object") stack.push(v);
        }
      }
    }
  } catch {}
  // Fallback: try parsing text for something like apify/xyz or numeric id (not guaranteed)
  if (fallbackText) {
    const m = fallbackText.match(/\(([A-Za-z0-9_-]+\/[A-Za-z0-9_-]+)\)/);
    if (m) return m[1];
  }
  return null;
}

function parseFirstCardFromText(text) {
  if (!text) return null;
  // Example line: "- # [Hello World Example](https://...) (apify/hello-world)"
  const header = text.match(/# \[([^\]]+)\]\([^\)]+\) \(([^\)]+)\)/);
  const name = header?.[1] || null;
  const id = header?.[2] || null;
  const descMatch = text.match(/\*\*Description:\*\*\s*([^\n]+)/);
  const description = descMatch ? descMatch[1].replace(/\s+/g, " ").trim() : null;
  if (!name && !id && !description) return null;
  return { name, id, description };
}

function shapeDetails(detailsJson, detailsText) {
  const out = { name: null, id: null, description: null };
  if (detailsJson && typeof detailsJson === "object") {
    out.id = detailsJson.id || detailsJson.actorId || detailsJson.slug || null;
    out.name = detailsJson.name || detailsJson.title || detailsJson.username || null;
    out.description = detailsJson.description || detailsJson.readme || null;
  }
  if (!out.description && typeof detailsText === "string") {
    out.description = detailsText;
  }
  // Clean description to one line short
  if (out.description) {
    out.description = String(out.description).replace(/\s+/g, " ").trim();
    if (out.description.length > 200) out.description = out.description.slice(0, 200) + "…";
  }
  return out;
}

async function main() {
  if (!APIFY_TOKEN) {
    console.error("ERR: APIFY_TOKEN not set in environment");
    process.exit(2);
  }
  const client = await connectClient();
  try {
    // Discover schemas to build valid args
    const { tools } = await client.listTools();
    const searchTool = tools.find((t) => t.name === "search-actors");
    const detailsTool = tools.find((t) => t.name === "get-actor-details");
    const searchSchema = searchTool?.inputSchema || searchTool?.input_schema || null;
    const detailsSchema = detailsTool?.inputSchema || detailsTool?.input_schema || null;

    const searchProps = searchSchema?.properties || {};
    const detailsProps = detailsSchema?.properties || {};

    // Pick acceptable keys based on schema
    const searchKey = ["q", "term", "query", "text", "name", "search"]
      .find((k) => Object.prototype.hasOwnProperty.call(searchProps, k));
    const limitKey = ["limit", "top", "take", "max"]
      .find((k) => Object.prototype.hasOwnProperty.call(searchProps, k));
    const searchArgs = {};
    if (searchKey) searchArgs[searchKey] = "hello world";
    if (limitKey) searchArgs[limitKey] = 3;
    const searchRes = await client.callTool({ name: "search-actors", arguments: searchArgs });
    const searchJson = extractJsonFromContent(searchRes);
    const searchText = extractTextFromContent(searchRes);
    const actorId = takeFirstActorId(searchJson, searchText);
    let firstCard = parseFirstCardFromText(searchText);
    let chosenId = actorId || firstCard?.id || null;
    if (!chosenId) {
      console.log(JSON.stringify({ error: "no_actor_found", searchPreview: searchJson ?? searchText ?? null }));
      return;
    }
    // Determine the correct id argument name
  const idKey = ["actorId", "actor", "id", "slug"]
      .find((k) => Object.prototype.hasOwnProperty.call(detailsProps, k)) || "actorId";
  const detailsArgs = {}; detailsArgs[idKey] = chosenId;
    const detailsRes = await client.callTool({ name: "get-actor-details", arguments: detailsArgs });
    const detailsJson = extractJsonFromContent(detailsRes);
    const detailsText = extractTextFromContent(detailsRes);
    const shaped = shapeDetails(detailsJson, detailsText);
    // Fill id if missing from details
  if (!shaped.id) shaped.id = chosenId;
    // If details didn't provide name/description, fallback to parsed card
    if (!shaped.name) shaped.name = firstCard?.name || null;
    if (!shaped.description) shaped.description = firstCard?.description || null;
    // If details returned a not-found text, prefer the card description
    if (shaped.description && /was not found/i.test(shaped.description) && firstCard?.description) {
      shaped.description = firstCard.description;
    }
  console.log(JSON.stringify({ name: shaped.name, id: shaped.id, description: shaped.description }));
  } finally {
    try { await client.close?.(); } catch {}
  }
}

main().catch((e) => { console.error("ERR:", e?.message || e); process.exit(1); });
