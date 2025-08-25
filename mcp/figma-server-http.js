#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";
import { randomUUID } from "node:crypto";
import { getTokens, setTokens } from "./figma/oauthStore.js";

// Allow overriding server port via env
const PORT = Number(process.env.FIGMA_HTTP_PORT || process.env.PORT || 3845);

// Util: load required env vars safely
function env(name) {
  const v = process.env[name];
  return v && v.trim() ? v : undefined;
}

const FIGMA_CLIENT_ID = env("FIGMA_CLIENT_ID");
const FIGMA_CLIENT_SECRET = env("FIGMA_CLIENT_SECRET");
const FIGMA_REDIRECT_URI = env("FIGMA_REDIRECT_URI");

// Track pending state from server-initiated flow (best-effort; do not hard-fail on mismatch)
let PENDING_OAUTH_STATE = undefined;

// Helper: Build OAuth URL
function buildAuthUrl() {
  if (!FIGMA_CLIENT_ID || !FIGMA_REDIRECT_URI) {
    return null;
  }
  const params = new URLSearchParams({
    client_id: FIGMA_CLIENT_ID,
    redirect_uri: FIGMA_REDIRECT_URI,
    scope: "files:read file_comments:read", // minimal common scopes; adjust as needed
    state: randomUUID(),
    response_type: "code",
  });
  PENDING_OAUTH_STATE = params.get("state") || undefined;
  return `https://www.figma.com/oauth?${params.toString()}`;
}

// Token exchange using Basic auth, trying api.figma.com then www.figma.com as fallback
async function exchangeCodeForTokens(code) {
  if (!FIGMA_CLIENT_ID || !FIGMA_CLIENT_SECRET || !FIGMA_REDIRECT_URI) {
    throw new Error("OAuth not configured");
  }
  const creds = Buffer.from(`${FIGMA_CLIENT_ID}:${FIGMA_CLIENT_SECRET}`).toString("base64");
  const headers = {
    Authorization: `Basic ${creds}`,
    "Content-Type": "application/x-www-form-urlencoded",
    Accept: "application/json",
  };
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: FIGMA_REDIRECT_URI,
  });
  const endpoints = [
    "https://api.figma.com/v1/oauth/token",
    "https://www.figma.com/api/oauth/token",
  ];
  let lastStatus = 0;
  let lastSnippet = "";
  for (const url of endpoints) {
    try {
      const resp = await fetch(url, { method: "POST", headers, body });
      if (resp.ok) {
        const data = await resp.json();
        const expiresIn = Number(data.expires_in ?? 3600);
        const expires_at = Math.floor(Date.now() / 1000) + (Number.isFinite(expiresIn) ? expiresIn : 3600);
        setTokens({
          access_token: data.access_token,
          refresh_token: data.refresh_token,
          expires_at,
        });
        return true;
      }
      lastStatus = resp.status;
      try {
        const txt = await resp.text();
        lastSnippet = (txt || "").slice(0, 300);
      } catch {}
    } catch (e) {
      // Network error — continue to next endpoint
    }
  }
  console.error(`oauth token exchange failed: ${lastStatus} ${lastSnippet}`);
  return false;
}

async function refreshTokensIfNeeded() {
  const t = getTokens();
  if (!t) return null;
  const now = Math.floor(Date.now() / 1000);
  // Refresh if expired or within 60s of expiry
  if (!t.expires_at || t.expires_at - now > 60) {
    return t;
  }
  if (!FIGMA_CLIENT_ID || !FIGMA_CLIENT_SECRET || !t.refresh_token) {
    console.error("[figma-oauth] missing client env or refresh token");
    return t;
  }
  const body = new URLSearchParams({
    client_id: FIGMA_CLIENT_ID,
    client_secret: FIGMA_CLIENT_SECRET,
    grant_type: "refresh_token",
    refresh_token: t.refresh_token,
  });
  const resp = await fetch("https://www.figma.com/api/oauth/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    console.error(`[figma-oauth] refresh failed: ${resp.status}`);
    return t; // keep old; caller may reauth
  }
  const data = await resp.json();
  const now2 = Math.floor(Date.now() / 1000);
  const expires_in = typeof data.expires_in === "number" ? data.expires_in : 3600;
  const merged = {
    access_token: data.access_token ?? t.access_token,
    refresh_token: data.refresh_token ?? t.refresh_token,
    token_type: data.token_type ?? t.token_type ?? "bearer",
    expires_at: now2 + expires_in,
  };
  setTokens(merged);
  return merged;
}

async function figmaApiGet(pathname) {
  const tokens = await refreshTokensIfNeeded();
  if (!tokens || !tokens.access_token) {
  throw new Error("Missing tokens. Run figma_auth and complete OAuth.");
  }
  const resp = await fetch(`https://api.figma.com${pathname}`, {
    headers: {
      Authorization: `Bearer ${tokens.access_token}`,
    },
  });
  if (!resp.ok) {
    throw new Error(`Figma API error ${resp.status}`);
  }
  return resp.json();
}

// Access token helper
async function getAccessToken() {
  const t = await refreshTokensIfNeeded();
  if (!t || !t.access_token) {
    throw new Error("Unauthorized. Run figma_auth");
  }
  return t.access_token;
}

// Force refresh helper (used when API returns 401)
async function refreshTokensForce() {
  const t = getTokens();
  if (!t || !t.refresh_token || !FIGMA_CLIENT_ID || !FIGMA_CLIENT_SECRET) return null;
  const body = new URLSearchParams({
    client_id: FIGMA_CLIENT_ID,
    client_secret: FIGMA_CLIENT_SECRET,
    grant_type: "refresh_token",
    refresh_token: t.refresh_token,
  });
  try {
    const resp = await fetch("https://www.figma.com/api/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    const now = Math.floor(Date.now() / 1000);
    const expires_in = typeof data.expires_in === "number" ? data.expires_in : 3600;
    const merged = {
      access_token: data.access_token ?? t.access_token,
      refresh_token: data.refresh_token ?? t.refresh_token,
      token_type: data.token_type ?? t.token_type ?? "bearer",
      expires_at: now + expires_in,
    };
    setTokens(merged);
    return merged;
  } catch (e) {
    console.error("[figma-oauth] forced refresh failed");
    return null;
  }
}

// Robust GET with retries/backoff and specific error handling
async function figmaGet(path, params) {
  const baseUrl = new URL(`https://api.figma.com${path}`);
  if (params && typeof params === "object") {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      usp.set(k, String(v));
    }
    const qs = usp.toString();
    if (qs) baseUrl.search = qs;
  }

  let attempts = 0;
  let didRefresh = false;
  const maxAttempts = 5;
  while (attempts < maxAttempts) {
    attempts += 1;
    let accessToken;
    try {
      accessToken = await getAccessToken();
    } catch (e) {
      // No tokens configured
      throw new Error("Unauthorized. Run figma_auth");
    }

    let resp;
    try {
      resp = await fetch(baseUrl, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          Accept: "application/json",
        },
      });
    } catch (e) {
      // Network errors: backoff
      const delay = Math.min(1000 * Math.pow(2, attempts - 1), 16000);
      await new Promise((r) => setTimeout(r, delay));
      continue;
    }

    if (resp.ok) {
      try {
        return await resp.json();
      } catch {
        throw new Error("Bad response from Figma API");
      }
    }

    const status = resp.status;

    // 401: try forced refresh once, then fail
    if (status === 401) {
      if (!didRefresh) {
        didRefresh = true;
        const refreshed = await refreshTokensForce();
        if (refreshed && refreshed.access_token) {
          // retry immediately without counting toward exponential delay
          continue;
        }
      }
      throw new Error("Unauthorized. Run figma_auth");
    }

    // 403 Forbidden
    if (status === 403) {
      throw new Error("Forbidden. No access");
    }

    // 404 Not found
    if (status === 404) {
      throw new Error("Not found. Bad file key or missing nodes");
    }

    // 429 Too Many Requests: respect Retry-After
    if (status === 429) {
      const ra = resp.headers.get("Retry-After");
      let waitMs = Math.min(1000 * Math.pow(2, attempts - 1), 16000);
      if (ra) {
        const secs = Number(ra);
        if (!Number.isNaN(secs)) {
          waitMs = Math.max(1000, secs * 1000);
        } else {
          const date = Date.parse(ra);
          if (!Number.isNaN(date)) {
            const delta = date - Date.now();
            if (delta > 0) waitMs = Math.min(Math.max(delta, 1000), 16000);
          }
        }
      }
      await new Promise((r) => setTimeout(r, waitMs));
      continue;
    }

    // 5xx retry with backoff
    if (status >= 500 && status <= 599) {
      const delay = Math.min(1000 * Math.pow(2, attempts - 1), 16000);
      await new Promise((r) => setTimeout(r, delay));
      continue;
    }

    // Other errors: include status and snippet
    let snippet = "";
    try {
      const text = await resp.text();
      snippet = text.slice(0, 200);
    } catch {}
    throw new Error(`Figma API error ${status}: ${snippet}`);
  }
  throw new Error("Figma API request failed after retries");
}

// Simple chunk helper
function chunk(arr, size = 50) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

// Build a fresh MCP server instance with the same tools for each session
function buildMcpServer() {
  const server = new McpServer({ name: "figma-mcp", version: "0.3.0" });
  server.registerTool(
  "figma_ping",
    { description: "Ping the Figma MCP server over HTTP.", inputSchema: {} },
    async () => ({ content: [{ type: "text", text: "pong" }] })
  );
  server.registerTool(
  "figma_auth",
    { description: "Start the OAuth flow for Figma.", inputSchema: {} },
    async () => {
      const url = buildAuthUrl();
      if (!url) {
        return {
          content: [
            { type: "text", text: "Figma OAuth not configured. Please set FIGMA_CLIENT_ID and FIGMA_REDIRECT_URI." },
          ],
        };
      }
      return { content: [{ type: "text", text: url }] };
    }
  );
  server.registerTool(
  "figma_me",
    { description: "Return the current Figma user (id, handle, email).", inputSchema: {} },
    async () => {
      try {
        const me = await figmaApiGet("/v1/me");
        const compact = {
          id: me.id ?? me.user_id ?? undefined,
          handle: me.handle ?? me.username ?? undefined,
          email: me.email ?? undefined,
        };
        return { content: [{ type: "text", text: JSON.stringify(compact) }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Error: ${(e && e.message) || String(e)}` }] };
      }
    }
  );

  // GET /v1/files/{fileKey}
  server.registerTool(
    "figma_file_get",
    {
      description: "Fetch a Figma file JSON by fileKey (optionally version).",
      inputSchema: {
        type: "object",
        properties: {
          fileKey: { type: "string" },
          version: { type: "string" },
        },
        required: ["fileKey"],
      },
    },
    async ({ fileKey, version }) => {
      if (!fileKey || typeof fileKey !== "string") {
        return { content: [{ type: "text", text: "fileKey is required" }] };
      }
      try {
        const url = `/v1/files/${encodeURIComponent(fileKey)}`;
        const json = await figmaGet(url, version ? { version } : undefined);
        return { content: [{ type: "json", json }] };
      } catch (e) {
        return { content: [{ type: "text", text: (e && e.message) || "Error" }] };
      }
    }
  );

  // GET /v1/files/{fileKey}/nodes?ids=...
  server.registerTool(
    "figma_file_nodes",
    {
      description: "Fetch specific node(s) from a Figma file by IDs (batched).",
      inputSchema: {
        type: "object",
        properties: {
          fileKey: { type: "string" },
          ids: { type: "array", items: { type: "string" } },
          chunkSize: { type: "integer", minimum: 1, maximum: 200, default: 50 },
        },
        required: ["fileKey", "ids"],
      },
    },
    async ({ fileKey, ids, chunkSize = 50 }) => {
      if (!fileKey || typeof fileKey !== "string") {
        return { content: [{ type: "text", text: "fileKey is required" }] };
      }
      if (!Array.isArray(ids) || ids.length === 0) {
        return { content: [{ type: "text", text: "ids is required (non-empty array)" }] };
      }
      const size = Math.max(1, Math.min(Number(chunkSize) || 50, 200));
      try {
        const batches = chunk(ids, size);
        const merged = { nodes: {} };
        for (const part of batches) {
          const res = await figmaGet(`/v1/files/${encodeURIComponent(fileKey)}/nodes`, { ids: part.join(",") });
          if (res && typeof res === "object" && res.nodes && typeof res.nodes === "object") {
            Object.assign(merged.nodes, res.nodes);
          }
        }
        return { content: [{ type: "json", json: merged }] };
      } catch (e) {
        return { content: [{ type: "text", text: (e && e.message) || "Error" }] };
      }
    }
  );
  return server;
}

// Create HTTP transport with required options (not used directly; sessions create their own transports)
const transport = new StreamableHTTPServerTransport({
  sessionIdGenerator: () => randomUUID(),
  enableDnsRebindingProtection: true,
  allowedHosts: [
    `localhost:${PORT}`,
    `127.0.0.1:${PORT}`,
    "localhost",
    "127.0.0.1",
  ],
  enableJsonResponse: true,
  onsessioninitialized: (sid) => {
    console.error(`[figma-mcp-http] session initialized: ${sid}`);
  },
});

// Session registry
const transports = new Map(); // sid -> transport
const servers = new Map(); // sid -> mcp server instance

// Wire Express routes
const app = express();
app.use(express.json({ limit: "4mb" }));
// Expose session header to browsers
app.use((req, res, next) => {
  res.setHeader("Access-Control-Expose-Headers", "mcp-session-id");
  next();
});

// Simple health endpoint
app.get("/health", (_req, res) => {
  res.status(200).json({ ok: true });
});

// OAuth Redirect Callback
app.get("/api/oauth/callback", async (req, res) => {
  try {
    const code = req.query.code;
    const state = req.query.state;
    if (!code || typeof code !== "string") {
      res.status(400).send("<html><body>Missing code</body></html>");
      return;
    }
    // State check: warn only, do not block
    if (typeof state === "string" && PENDING_OAUTH_STATE && state !== PENDING_OAUTH_STATE) {
      console.error(`[figma-oauth] state mismatch: expected ${PENDING_OAUTH_STATE}, got ${state}`);
    }
    PENDING_OAUTH_STATE = undefined;

    const ok = await exchangeCodeForTokens(code);
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    if (ok) {
      res.end("<html><body><p>Auth complete, you can close this tab.</p></body></html>");
    } else {
      res.status(502).end("<html><body><p>Auth failed — please retry from the app.</p></body></html>");
    }
  } catch (e) {
    console.error("[figma-oauth] callback error");
    res.status(500).send("<html><body>Auth failed — please retry from the app.</body></html>");
  }
});

// POST /mcp handles client→server JSON-RPC requests and creates sessions if needed
app.post("/mcp", async (req, res) => {
  try {
    let t;
    const sid = req.headers["mcp-session-id"]; // case-insensitive in Node
    if (sid && transports.has(sid)) {
      t = transports.get(sid);
    } else {
      // Create a new transport and server for this session
  t = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        enableDnsRebindingProtection: true,
  allowedHosts: [
    `localhost:${PORT}`,
    `127.0.0.1:${PORT}`,
    "localhost",
    "127.0.0.1",
  ],
        enableJsonResponse: true,
        onsessioninitialized: (newSid) => {
          // Map session on first initialize
          transports.set(newSid, t);
          const hasServer = servers.has(newSid);
          if (!hasServer) {
            // already connected below; just note mapping
          }
          console.error(`[figma-mcp-http] session started ${newSid}`);
        },
      });
      const server = buildMcpServer();
      await server.connect(t);
      // We don't yet know sessionId until first initialize; onsessioninitialized will map it.
      // Keep a temporary marker under undefined won't help; store on first callback.
      // Retain a weak reference by attaching to transport until mapped
      t.__server = server; // internal note
    }
    await t.handleRequest(req, res, req.body);
  } catch (e) {
    console.error("POST /mcp error", e);
    res
      .status(500)
      .json({ jsonrpc: "2.0", error: { code: -32000, message: "Internal error" }, id: null });
  }
});

// GET /mcp requires a valid session id
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

// DELETE /mcp ends sessions
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
      servers.delete(sid);
    }
    res.status(204).end();
  } catch (e) {
    console.error("DELETE /mcp error", e);
    res.status(500).end();
  }
});

// Start HTTP listener (per-session transports created on demand in POST)
app.listen(PORT, () => {
  console.error(`[figma-mcp-http] listening on http://127.0.0.1:${PORT}/mcp`);
});
