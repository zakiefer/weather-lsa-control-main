#!/usr/bin/env node
// Firecrawl discovery logger: per-run JSON + rolling JSONL (search-only)
// Usage: node tools/firecrawl_discovery_log.mjs "<query>" [limit]

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");

const argvQuery = process.argv[2] || "site:example.com docs";
const argvLimit = Number(process.argv[3] || 5) || 5;

function slugify(q) {
  return String(q).toLowerCase().replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "");
}

function ensureDir(p) { fs.mkdirSync(p, { recursive: true }); }

function extractJson(result) {
  try {
    for (const c of result?.content ?? []) if (c?.type === "json" && c.json) return c.json;
    for (const c of result?.content ?? []) if (c?.type === "text" && typeof c.text === "string") {
      try { return JSON.parse(c.text.trim()); } catch {}
    }
  } catch {}
  return null;
}

async function connectFirecrawl() {
  // Start the stdio server via the repo script; relies on FIRECRAWL_API_KEY in env or .env.local
  // We use StdioClientTransport to communicate with the child process.
  const command = path.resolve(REPO_ROOT, "scripts/mcp/firecrawl_stdio.sh");
  const transport = new StdioClientTransport({
    command,
    args: [],
    env: process.env,
  });
  const client = new Client({ name: "firecrawl-discovery-log", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

async function search(query, limit) {
  const client = await connectFirecrawl();
  try {
    // Prefer explicit tool if listed, otherwise attempt by name.
    const { tools } = await client.listTools().catch(() => ({ tools: [] }));
    const searchTool = tools.find((t) => t.name === "firecrawl_search") || tools.find((t) => /search/i.test(t.name));
    const name = searchTool?.name || "firecrawl_search";
    const args = { query, limit };
    const res = await client.callTool({ name, arguments: args }).catch(() => null);
    const json = res ? extractJson(res) : null;
    // Normalize to expected top: [{title,url,description?}]
    const top = [];
    if (Array.isArray(json)) {
      for (const it of json.slice(0, limit)) {
        top.push({ title: it.title || it.name || it.url || "", url: it.url || it.link || "", description: it.description || it.snippet || undefined });
      }
    } else if (json?.results) {
      for (const it of (json.results || []).slice(0, limit)) {
        top.push({ title: it.title || it.name || it.url || "", url: it.url || it.link || "", description: it.description || it.snippet || undefined });
      }
    }
    return top;
  } finally {
    try { await client.close?.(); } catch {}
  }
}

async function main() {
  const ts = new Date().toISOString();
  const source = "firecrawl";
  const notes = "search results only; run Crawl & Extract task for full content";

  // Ensure FIRECRAWL_API_KEY is present; attempt minimal .env.local load if missing
  if (!process.env.FIRECRAWL_API_KEY) {
    try {
      const envPath = path.join(REPO_ROOT, ".env.local");
      if (fs.existsSync(envPath)) {
        const data = fs.readFileSync(envPath, "utf8");
        for (const line of data.split(/\r?\n/)) {
          const m = line.match(/^\s*FIRECRAWL_API_KEY\s*=\s*(.*)\s*$/);
          if (m) { process.env.FIRECRAWL_API_KEY = m[1].replace(/\r$/, ""); break; }
        }
      }
    } catch {}
  }

  let top = [];
  try {
    top = await search(argvQuery, argvLimit);
  } catch {
    // tolerate failures
  }

  const record = { ts, query: argvQuery, limit: argvLimit, top, source, notes };
  const outDir = path.join(REPO_ROOT, "logs", "firecrawl");
  ensureDir(outDir);
  const perRun = path.join(outDir, `${ts.replace(/[:]/g, "-")}_${slugify(argvQuery)}.json`);
  fs.writeFileSync(perRun, JSON.stringify(record, null, 2));
  const jsonlPath = path.join(outDir, "discoveries.jsonl");
  fs.appendFileSync(jsonlPath, JSON.stringify(record) + "\n");

  console.log(`saved ${path.relative(REPO_ROOT, perRun)}; appended discoveries.jsonl — top: ${top.length}`);
  process.exit(0);
}

main();
