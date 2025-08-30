#!/usr/bin/env node
// Discover candidate Actors for a query, fetch details, and print a compact plan scaffold.
// Env/args:
//  - APIFY_QUERY (or pass as first arg)
// Output: one JSON line with fields used to render a plan.

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const APIFY_URL = process.env.APIFY_MCP_URL || "https://mcp.apify.com";
const APIFY_TOKEN = process.env.APIFY_TOKEN || "";
const QUERY = process.env.APIFY_QUERY || process.argv.slice(2).join(" ") || "product page scraper";

function extractJsonFromContent(result) {
  try {
    const items = result?.content ?? [];
    for (const c of items) if (c?.type === "json" && c.json) return c.json;
    for (const c of items) if (c?.type === "text" && typeof c.text === "string") {
      try { return JSON.parse(c.text.trim()); } catch {}
    }
  } catch {}
  return null;
}

function extractTextFromContent(result) {
  const items = result?.content ?? [];
  const t = items.find((c) => c?.type === "text")?.text;
  return typeof t === "string" ? t : null;
}

function parseFirstCardFromText(text) {
  if (!text) return null;
  const header = text.match(/# \[([^\]]+)\]\([^\)]+\) \(([^\)]+)\)/);
  const name = header?.[1] || null;
  const id = header?.[2] || null;
  const descMatch = text.match(/\*\*Description:\*\*\s*([^\n]+)/);
  const description = descMatch ? descMatch[1].replace(/\s+/g, " ").trim() : null;
  if (!name && !id && !description) return null;
  return { name, id, description };
}

async function connectClient() {
  const url = new URL(APIFY_URL);
  const transport = new StreamableHTTPClientTransport(url, {
    authProvider: { tokens: async () => ({ access_token: APIFY_TOKEN }) },
    requestInit: { headers: { "content-type": "application/json", accept: "application/json, text/event-stream" } },
  });
  const client = new Client({ name: "apify-search-plan", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

function findFirstSchemaNode(obj) {
  const seen = new Set();
  const stack = [obj];
  while (stack.length) {
    const cur = stack.pop();
    if (!cur || typeof cur !== "object") continue;
    if (seen.has(cur)) continue; seen.add(cur);
    if (cur.type === "object" && cur.properties && typeof cur.properties === "object") return cur;
    for (const v of Object.values(cur)) if (v && typeof v === "object") stack.push(v);
  }
  return null;
}

function condenseSchema(schema) {
  if (!schema) return null;
  const required = Array.isArray(schema.required) ? schema.required : [];
  const fields = Object.entries(schema.properties || {}).map(([k, v]) => ({
    key: k,
    type: Array.isArray(v?.type) ? v.type.join("|") : v?.type || (v?.items?.type ? `${v.items.type}[]` : "unknown"),
    required: required.includes(k),
  }));
  return { required, fields };
}

function makePayloads(schema) {
  if (!schema) return { mwp: null, safe: null };
  const req = Array.isArray(schema.required) ? schema.required : [];
  const props = schema.properties || {};
  const base = {};
  for (const key of req) {
    const s = props[key] || {};
    const t = Array.isArray(s.type) ? s.type[0] : s.type;
    const lname = key.toLowerCase();
    if (t === "array") {
      const it = Array.isArray(s.items?.type) ? s.items.type[0] : s.items?.type;
      if (it === "string") {
        base[key] = [/url|link/.test(lname) ? "https://example.com/product/123" : "example"];
      } else if (it === "object" && s.items?.properties?.url) {
        base[key] = [{ url: "https://example.com/product/123" }];
      } else {
        base[key] = [1];
      }
    } else if (t === "number" || t === "integer") {
      base[key] = 1;
    } else if (t === "boolean") {
      base[key] = false;
    } else if (t === "object" && props[key]?.properties?.url) {
      base[key] = { url: "https://example.com/product/123" };
    } else {
      base[key] = /url|link/.test(lname) ? "https://example.com/product/123" : "example";
    }
  }
  const mwp = base;
  const safe = JSON.parse(JSON.stringify(base));
  // Try to keep it non-destructive
  for (const k of Object.keys(safe)) {
    if (/max|limit|items/i.test(k) && typeof safe[k] === "number") safe[k] = Math.min(1, safe[k] || 1);
  }
  return { mwp, safe };
}

async function main() {
  if (!APIFY_TOKEN) {
    console.error("ERR: APIFY_TOKEN not set in environment");
    process.exit(2);
  }
  const client = await connectClient();
  try {
    const { tools } = await client.listTools();
    const searchTool = tools.find((t) => t.name === "search-actors");
    const detailsTool = tools.find((t) => t.name === "get-actor-details");
    const searchSchema = searchTool?.inputSchema || searchTool?.input_schema || null;
    const detailsSchema = detailsTool?.inputSchema || detailsTool?.input_schema || null;
    const searchProps = searchSchema?.properties || {};
    const detailsProps = detailsSchema?.properties || {};

    const searchKey = ["q", "term", "query", "text", "name", "search"].find((k) => k in searchProps) || "query";
    const limitKey = ["limit", "top", "take", "max"].find((k) => k in searchProps) || "limit";
    const searchArgs = {}; searchArgs[searchKey] = QUERY; searchArgs[limitKey] = 3;
    const searchRes = await client.callTool({ name: "search-actors", arguments: searchArgs });
    const searchJson = extractJsonFromContent(searchRes);
    const searchText = extractTextFromContent(searchRes);

    let candidate = null;
    if (searchJson?.items?.length) {
      const it = searchJson.items[0];
      candidate = { id: it.id || it.actorId || null, name: it.name || it.title || null, desc: it.description || null };
    }
    if (!candidate) {
      const card = parseFirstCardFromText(searchText);
      if (card) candidate = { id: card.id, name: card.name, desc: card.description };
    }
    if (!candidate?.id) {
      console.log(JSON.stringify({ ok: false, reason: "no_candidate", query: QUERY, preview: searchJson ?? searchText ?? null }));
      return;
    }

    const idKey = ["actorId", "actor", "id", "slug"].find((k) => k in detailsProps) || "actorId";
    const detailsArgs = {}; detailsArgs[idKey] = candidate.id;
    const detailsRes = await client.callTool({ name: "get-actor-details", arguments: detailsArgs });
    const detailsJson = extractJsonFromContent(detailsRes);
    const detailsText = extractTextFromContent(detailsRes);

    // Try to locate a JSON schema within details
    const schema = findFirstSchemaNode(detailsJson);
    const condensed = condenseSchema(schema);
    const payloads = makePayloads(schema);

    console.log(JSON.stringify({
      ok: true,
      query: QUERY,
      actor: { id: candidate.id, name: candidate.name, description: candidate.desc },
      inputSchema: condensed,
      mwp: payloads.mwp,
      safe: payloads.safe,
      notes: !schema ? "Input schema not found in MCP response; use README to refine." : undefined,
    }));
  } finally {
    try { await client.close?.(); } catch {}
  }
}

main().catch((e) => { console.error("ERR:", e?.message || e); process.exit(1); });
