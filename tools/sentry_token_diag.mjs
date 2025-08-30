#!/usr/bin/env node
// Diagnose SENTRY_AUTH_TOKEN presence and format without shell loaders.
// Checks env and .env.local, prints a compact JSON report.

import fs from "fs";
import path from "path";

function parseDotEnv(content) {
  const out = {};
  for (const line of content.split(/\r?\n/)) {
    if (!line || /^\s*#/.test(line)) continue;
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!m) continue;
    let [, k, v] = m;
    // Strip optional quotes
    if ((v.startsWith("\"") && v.endsWith("\"")) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[k] = v;
  }
  return out;
}

function summarizeToken(src, token) {
  const len = token?.length || 0;
  const head = token ? token.slice(0, 4) : "";
  const tail = token ? token.slice(-4) : "";
  const hasNewline = token ? /\n|\r/.test(token) : false;
  const hasQuotes = token ? /['"]/g.test(token) : false;
  const looksLikeDSN = token ? /https?:\/\/.+@.+sentry\.io\//.test(token) : false;
  const isPlaceholder = token === "REPLACE_WITH_PERSONAL_AUTH_TOKEN";
  return { src, present: !!token, len, head, tail, hasNewline, hasQuotes, looksLikeDSN, isPlaceholder };
}

function main() {
  const envTok = process.env.SENTRY_AUTH_TOKEN || "";
  const envSummary = summarizeToken("env", envTok);

  let fileTok = "";
  let fileSummary = { src: "envfile", error: "not-read" };
  try {
    const envPath = path.resolve(process.cwd(), ".env.local");
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, "utf8");
      const parsed = parseDotEnv(content);
      fileTok = parsed.SENTRY_AUTH_TOKEN || "";
      fileSummary = summarizeToken(".env.local", fileTok);
    } else {
      fileSummary = { src: ".env.local", present: false, note: "missing" };
    }
  } catch (e) {
    fileSummary = { src: ".env.local", error: String(e?.message || e) };
  }

  const advice = [];
  if (!envSummary.present && (!fileTok || fileTok === "REPLACE_WITH_PERSONAL_AUTH_TOKEN")) {
    advice.push("Set SENTRY_AUTH_TOKEN to a Personal Auth Token (not a DSN) from sentry.io > Settings > Account > Auth Tokens.");
  }
  if (envSummary.looksLikeDSN || fileSummary.looksLikeDSN) {
    advice.push("Provided value looks like a DSN; the MCP requires a Personal Auth Token, not a DSN URL.");
  }
  if (envSummary.hasNewline || fileSummary.hasNewline) {
    advice.push("Token contains newline characters; remove them so it is a single line.");
  }
  if (envSummary.isPlaceholder || fileSummary.isPlaceholder) {
    advice.push("Replace placeholder token with a real Personal Auth Token.");
  }

  console.log(JSON.stringify({ env: envSummary, file: fileSummary, advice }));
}

main();
