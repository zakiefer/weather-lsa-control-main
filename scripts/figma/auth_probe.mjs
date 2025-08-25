import { randomUUID } from "node:crypto";
import fs from "node:fs";

// Load env from .env.local if present (non-fatal if missing)
try {
  if (fs.existsSync(".env.local")) {
    const lines = fs.readFileSync(".env.local","utf8").split(/\r?\n/);
    for (const ln of lines) {
      const m = ln.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (!m) continue;
      const k = m[1];
      let v = m[2];
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1,-1);
      if (!process.env[k]) process.env[k] = v;
    }
  }
} catch {}

const TOK = ".secrets/figma_tokens.json";

function tokenStatus() {
  try {
    if (!fs.existsSync(TOK)) return "missing";
    const j = JSON.parse(fs.readFileSync(TOK, "utf8"));
    const exp = Number(j.expires_at || 0); // seconds
    const now = Math.floor(Date.now()/1000);
    if (exp > now + 60) return "present";
    return "expired";
  } catch {
    return "missing";
  }
}

const status = tokenStatus();
console.log(status);
if (status === "present") process.exit(0);

const { FIGMA_CLIENT_ID, FIGMA_REDIRECT_URI } = process.env;
if (!FIGMA_CLIENT_ID || !FIGMA_REDIRECT_URI) {
  console.log("NOAUTHENV");
  process.exit(0);
}

const encRedirect = encodeURIComponent(FIGMA_REDIRECT_URI);
const state = randomUUID();
const url = `https://www.figma.com/oauth?client_id=${encodeURIComponent(FIGMA_CLIENT_ID)}&redirect_uri=${encRedirect}&scope=files:read%20file_comments:read&state=${state}&response_type=code`;
console.log(url);
