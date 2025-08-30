#!/usr/bin/env node
// Hybrid Apify discovery logger: per-run JSON + rolling JSONL
// Usage: node tools/apify_discovery_log.mjs "<query>" [limit]

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");

const APIFY_URL = process.env.APIFY_MCP_URL || "https://mcp.apify.com";
let APIFY_TOKEN = process.env.APIFY_TOKEN || "";

const argvQuery = process.argv[2] || "product page scraper";
const argvLimit = Number(process.argv[3] || 3) || 3;

function slugify(q) {
  return String(q).toLowerCase().replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "");
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

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
  return { id, name, description };
}

async function connectClient() {
  const url = new URL(APIFY_URL);
  const transport = new StreamableHTTPClientTransport(url, {
    authProvider: { tokens: async () => ({ access_token: APIFY_TOKEN }) },
    requestInit: { headers: { "content-type": "application/json", accept: "application/json, text/event-stream" } },
  });
  const client = new Client({ name: "apify-discovery-log", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

function condenseSchema(schemaObj) {
  try {
    if (!schemaObj || typeof schemaObj !== "object") return [];
    const props = schemaObj.properties || {};
    const required = new Set(schemaObj.required || []);
    const out = [];
    for (const [name, p] of Object.entries(props)) {
      const type = Array.isArray(p?.type) ? p.type.join("|") : p?.type || (p?.anyOf ? "anyOf" : (p?.oneOf ? "oneOf" : "object"));
      let desc = String(p?.description || "");
      if (desc.length > 100) desc = desc.slice(0, 100).trimEnd() + "…";
      out.push({ name, type, required: required.has(name) || !!p?.required, desc });
    }
    return out;
  } catch {
    return [];
  }
}

function extractJsonBlocksFromText(text) {
  if (!text) return [];
  const blocks = [];
  try {
    const fenceRe = /```(?:json|javascript)?\n([\s\S]*?)```/g;
    let m;
    while ((m = fenceRe.exec(text))) {
      const raw = m[1].trim();
      try {
        const obj = JSON.parse(raw);
        blocks.push(obj);
      } catch {}
    }
  } catch {}
  return blocks;
}

function findSchemaInDetails(dJson, dText) {
  // Try common fields first
  const candidates = [
    dJson?.inputSchema,
    dJson?.input_schema,
    dJson?.schema,
    dJson?.input?.schema,
    dJson?.defaultRun?.inputSchema,
    dJson?.defaultRun?.input_schema,
  ].filter(Boolean);
  for (const c of candidates) {
    const condensed = condenseSchema(c);
    if (condensed.length) return condensed;
  }
  // Try text JSON code blocks
  const blocks = extractJsonBlocksFromText(dText || "");
  for (const b of blocks) {
    if (b && typeof b === "object" && (b.properties || b.required)) {
      const condensed = condenseSchema(b);
      if (condensed.length) return condensed;
    }
  }
  return [];
}

async function discover(query, limit) {
  const client = await connectClient();
  try {
    const { tools } = await client.listTools();
    const searchTool = tools.find((t) => t.name === "search-actors");
    const detailsTool = tools.find((t) => t.name === "get-actor-details");
    const searchSchema = searchTool?.inputSchema || searchTool?.input_schema || {};
    const detailsSchema = detailsTool?.inputSchema || detailsTool?.input_schema || {};
    const sProps = searchSchema.properties || {};
    const dProps = detailsSchema.properties || {};

    const qKey = ["q", "term", "query", "text", "name", "search"].find((k) => k in sProps) || "query";
    const limKey = ["limit", "top", "take", "max"].find((k) => k in sProps) || "limit";
    const args = {}; args[qKey] = query; args[limKey] = limit;
    const sRes = await client.callTool({ name: "search-actors", arguments: args });
    const sJson = extractJsonFromContent(sRes);
    const sText = extractTextFromContent(sRes);

    const top_candidates = [];
    if (sJson?.items?.length) {
      for (const it of sJson.items.slice(0, limit)) {
        top_candidates.push({ id: it.id || it.actorId || null, name: it.name || it.title || null, description: it.description || null });
      }
    } else {
      const card = parseFirstCardFromText(sText);
      if (card) top_candidates.push(card);
    }

    let chosen = null;
    let input_schema_condensed = [];
    if (top_candidates.length && (top_candidates[0]?.id || top_candidates[0]?.name)) {
      const idKey = ["actorId", "actor", "id", "slug"].find((k) => k in dProps) || "actorId";
      const dArgs = {}; dArgs[idKey] = top_candidates[0].id || top_candidates[0].name;
      const dRes = await client.callTool({ name: "get-actor-details", arguments: dArgs }).catch(() => null);
      const dJson = dRes ? extractJsonFromContent(dRes) : null;
      const dText = dRes ? extractTextFromContent(dRes) : null;
      let cid = dJson?.id || dJson?.actorId || top_candidates[0].id || null;
      let cname = dJson?.name || top_candidates[0].name || null;
      let cdesc = dJson?.description || dText || top_candidates[0].description || null;
      if (cdesc) cdesc = String(cdesc).replace(/\s+/g, " ").trim();
      chosen = { id: cid, name: cname, description: cdesc };
      // Try to capture condensed input schema
      input_schema_condensed = findSchemaInDetails(dJson, dText);
    }

    return { top_candidates, chosen, input_schema_condensed };
  } finally {
    try { await client.close?.(); } catch {}
  }
}

async function main() {
  // Lightweight .env.local loader for APIFY_TOKEN if missing
  if (!APIFY_TOKEN) {
    try {
      const envPath = path.join(REPO_ROOT, ".env.local");
      if (fs.existsSync(envPath)) {
        const data = fs.readFileSync(envPath, "utf8");
        for (const line of data.split(/\r?\n/)) {
          const m = line.match(/^\s*APIFY_TOKEN\s*=\s*(.*)\s*$/);
          if (m) {
            APIFY_TOKEN = m[1].replace(/\r$/, "");
            break;
          }
        }
      }
    } catch {}
  }
  const ts = new Date().toISOString();
  const source = "apify";
  const notes = "schema not returned via MCP; consult README if needed";

  let top_candidates = [];
  let chosen = null;
  let input_schema_condensed = [];
  if (APIFY_TOKEN) {
    try {
      const r = await discover(argvQuery, argvLimit);
      top_candidates = r.top_candidates || [];
      chosen = r.chosen || null;
      input_schema_condensed = r.input_schema_condensed || [];
    } catch (e) {
      // Tolerate failures; still emit log with empties
    }
  }

  const record = { ts, query: argvQuery, limit: argvLimit, top_candidates, chosen, input_schema_condensed, source, notes };

  const outDir = path.join(REPO_ROOT, "logs", "apify");
  ensureDir(outDir);
  const perRun = path.join(outDir, `${ts.replace(/[:]/g, "-")}_${slugify(argvQuery)}.json`);
  fs.writeFileSync(perRun, JSON.stringify(record, null, 2));

  const jsonlPath = path.join(outDir, "discoveries.jsonl");
  fs.appendFileSync(jsonlPath, JSON.stringify(record) + "\n");

  const chosenId = record.chosen?.id || record.chosen?.name || "none";
  const msg = `saved ${path.relative(REPO_ROOT, perRun)}; appended discoveries.jsonl — chosen: ${chosenId}`;
  console.log(msg);
  process.exit(0);
}

main();
