#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { spawn } from "child_process";
import fs from "fs";
import http from "http";
import path from "path";
import { z } from "zod";

const TOKENS_PATH = path.resolve(process.cwd(), ".secrets", "figma_tokens.json");
const FIGMA_TOKEN_URL = "https://www.figma.com/api/oauth/token";
const FIGMA_ME_URL = "https://api.figma.com/v1/me";

function ensureSecretsDir() {
  const dir = path.dirname(TOKENS_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function readTokens() {
  try {
    return JSON.parse(fs.readFileSync(TOKENS_PATH, "utf8"));
  } catch {
    return null;
  }
}

function writeTokens(tokens) {
  ensureSecretsDir();
  fs.writeFileSync(TOKENS_PATH, JSON.stringify(tokens, null, 2));
}

function isExpired(tokens) {
  if (!tokens || !tokens.obtained_at || !tokens.expires_in) return true;
  const expireAt = tokens.obtained_at + tokens.expires_in * 1000 - 30000; // 30s buffer
  return Date.now() >= expireAt;
}

async function exchangeCodeForTokens({ code, clientId, clientSecret, redirectUri }) {
  const res = await fetch(FIGMA_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      code,
      grant_type: "authorization_code",
    }),
  });
  if (!res.ok) throw new Error(`Token exchange failed ${res.status}`);
  const data = await res.json();
  data.obtained_at = Date.now();
  writeTokens(data);
  return data;
}

async function refreshTokens({ refresh_token, clientId, clientSecret, redirectUri }) {
  const res = await fetch(FIGMA_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      refresh_token,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) throw new Error(`Refresh failed ${res.status}`);
  const data = await res.json();
  data.obtained_at = Date.now();
  writeTokens(data);
  return data;
}

async function getAccessToken() {
  const clientId = process.env.FIGMA_CLIENT_ID;
  const clientSecret = process.env.FIGMA_CLIENT_SECRET;
  const redirectUri = process.env.FIGMA_REDIRECT_URI;
  let tokens = readTokens();
  if (!tokens || isExpired(tokens)) {
    if (tokens?.refresh_token) {
      tokens = await refreshTokens({ refresh_token: tokens.refresh_token, clientId, clientSecret, redirectUri });
    } else {
      throw new Error("Not authorized yet. Run the auth tool first.");
    }
  }
  return tokens.access_token;
}

function openInBrowser(url) {
  const cmd = process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
  try {
    spawn(cmd, [url], { stdio: "ignore", shell: true, detached: true }).unref();
  } catch (e) {
    console.error(`[figma-mcp] Failed to open browser: ${e instanceof Error ? e.message : String(e)}`);
  }
}

async function startAuthFlow() {
  const clientId = process.env.FIGMA_CLIENT_ID;
  const clientSecret = process.env.FIGMA_CLIENT_SECRET;
  const redirectUri = process.env.FIGMA_REDIRECT_URI;
  if (!clientId || !clientSecret || !redirectUri) throw new Error("Missing FIGMA env vars.");

  // secure nonce
  const state = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const authUrl = `https://www.figma.com/oauth?client_id=${encodeURIComponent(clientId)}&redirect_uri=${encodeURIComponent(
    redirectUri
  )}&scope=file_read&state=${encodeURIComponent(state)}&response_type=code`;

  // short lived local HTTP server for the OAuth callback path
  const { hostname, port, pathname } = new URL(redirectUri);
  const server = http.createServer(async (req, res) => {
    try {
      if (req.method !== "GET") {
        res.statusCode = 405;
        res.end("Method not allowed");
        return;
      }
      const url = new URL(req.url, `http://${req.headers.host}`);
      if (url.pathname !== pathname) {
        res.statusCode = 404;
        res.end("Not Found");
        return;
      }
      const code = url.searchParams.get("code");
      const gotState = url.searchParams.get("state");
      if (!code || gotState !== state) {
        res.statusCode = 400;
        res.end("Invalid auth response");
        return;
      }
      await exchangeCodeForTokens({ code, clientId, clientSecret, redirectUri });
      res.statusCode = 200;
      res.setHeader("content-type", "text/plain");
      res.end("Figma connected. You can close this tab.");
    } catch (e) {
      console.error(`[figma-mcp] OAuth error: ${e instanceof Error ? e.message : String(e)}`);
      res.statusCode = 500;
      res.end("OAuth error");
    } finally {
      setTimeout(() => server.close(), 500);
    }
  });

  await new Promise((resolve, reject) => {
    server.listen({ host: hostname, port: Number(port) || 80 }, (err) => (err ? reject(err) : resolve()));
  });

  openInBrowser(authUrl);
  return { ok: true };
}

// Create MCP server
const server = new McpServer({ name: "figma-mcp", version: "0.1.0" });

// Register tools
server.registerTool(
  "figma.ping",
  { description: "Ping the Figma MCP server.", inputSchema: z.object({}) },
  async () => ({ content: [{ type: "text", text: "pong" }] })
);

server.registerTool(
  "figma.auth",
  { description: "Start OAuth flow to connect to Figma.", inputSchema: z.object({}) },
  async () => {
    await startAuthFlow();
    return {
      content: [
        { type: "text", text: "Opened Figma sign in. Complete it in the browser, then run figma.me." },
      ],
    };
  }
);

server.registerTool(
  "figma.me",
  { description: "Fetch the current Figma user (requires prior auth).", inputSchema: z.object({}) },
  async () => {
    const token = await getAccessToken();
    const res = await fetch(FIGMA_ME_URL, { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) {
      return { content: [{ type: "text", text: `Figma /me failed with status ${res.status}` }], isError: true };
    }
    const me = await res.json();
    return { content: [{ type: "text", text: JSON.stringify(me, null, 2) }] };
  }
);

// Connect stdio transport and start serving
const transport = new StdioServerTransport();
await server.connect(transport);
