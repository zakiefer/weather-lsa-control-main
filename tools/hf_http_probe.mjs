#!/usr/bin/env node
// Hardened Hugging Face MCP HTTP probe
// - Pre-init health poll
// - Initialize with Accept: application/json only
// - Strict timeouts and clear failures
// - Exit non-zero on failure, zero on success

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const HF_URL = process.env.HUGGINGFACE_MCP_URL || "http://127.0.0.1:3865/mcp";
const HEALTH_URL = process.env.HUGGINGFACE_HEALTH_URL || "http://127.0.0.1:3865/healthz";
// Allow overriding timeouts via env for stricter probes
const INIT_TIMEOUT_MS = Number(process.env.HF_INIT_TIMEOUT_MS || 10_000);
const CALL_TIMEOUT_MS = Number(process.env.HF_CALL_TIMEOUT_MS || 10_000);

function pickTool(tools) {
  const names = tools.map((t) => t.name);
  if (names.includes("hf_sentiment")) return "hf_sentiment";
  if (names.includes("hf_search_models")) return "hf_search_models";
  if (names.includes("hf_whoami")) return "hf_whoami";
  return names[0];
}

function toolNames(tools) {
  return tools.map((t) => t.name);
}

async function waitForHealth(url, timeoutMs = 10_000, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { headers: { accept: "application/json" } });
      if (res.ok) {
        const j = await res.json().catch(() => ({}));
        if (j && j.ok === true) return true;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    promise
      .then((v) => {
        clearTimeout(t);
        resolve(v);
      })
      .catch((e) => {
        clearTimeout(t);
        reject(e);
      });
  });
}

function extractPayload(res) {
  // Extract structured payload from MCP response content blocks if present
  try {
    if (res && Array.isArray(res.content) && res.content.length) {
      const jsons = res.content
        .map((c) => (c && c.type === "json" && c.json !== undefined ? c.json : undefined))
        .filter((j) => j !== undefined);
      if (jsons.length === 1) return jsons[0];
      if (jsons.length > 1) return jsons;

      const texts = res.content
        .map((c) => (typeof c?.text === "string" ? c.text.trim() : ""))
        .filter((t) => t.length > 0);
      // If multiple text chunks, first try to join and parse as a single JSON payload
      if (texts.length > 1) {
        const joined = texts.join("");
        try {
          return JSON.parse(joined);
        } catch {}
      }
      if (texts.length === 1) {
        const s = texts[0];
        try {
          return JSON.parse(s);
        } catch {
          return s; // plain text
        }
      }
      if (texts.length > 1) {
        return texts.map((s) => {
          try {
            return JSON.parse(s);
          } catch {
            return s; // plain text item
          }
        });
      }
    }
  } catch {}
  return res;
}

async function connectClient() {
  const url = new URL(HF_URL);
  // Server requires accepting both json and event-stream; initialize remains non-SSE in practice
  const headers = { "content-type": "application/json", accept: "application/json, text/event-stream" };
  const transport = new StreamableHTTPClientTransport(url, {
    requestInit: { headers },
    initTimeoutMs: INIT_TIMEOUT_MS,
    requestTimeoutMs: CALL_TIMEOUT_MS,
    maxRetries: 1,
  });
  const client = new Client({ name: "hf-http-probe", version: "1.1.0" });
  await client.connect(transport);
  return client;
}

function parseArgs(argv) {
  const out = { check: undefined };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--check" && i + 1 < argv.length) {
      out.check = argv[i + 1];
      i++;
    }
  }
  return out;
}

async function runTranslateSingle(client) {
  const src = process.env.TX_SRC || "en";
  const tgt = process.env.TX_TGT || "es";
  const text = process.env.TX_TEXT || "hello world";
  const args = { name: "hf_translate", arguments: { text, src, tgt } };
  const t0 = Date.now();
  const raw = await withTimeout(client.callTool(args), CALL_TIMEOUT_MS, "call hf_translate (single)");
  const out = extractPayload(raw);
  const ms = Date.now() - t0;
  if (out && typeof out === "object" && out.error === "unavailable") {
    console.log(JSON.stringify({ check: "hf_translate_single", status: "FAIL", reason: out.message || "unavailable", src, tgt, ms }));
    return { status: "FAIL", out };
  }
  if (typeof out !== "string" || out.length === 0) {
    console.log(JSON.stringify({ check: "hf_translate_single", status: "FAIL", reason: "empty output", src, tgt, ms }));
    return { status: "FAIL", out };
  }
  // Basic sanity: translated text shouldn't exactly equal input (best-effort heuristic)
  const sane = (out || "").trim().toLowerCase() !== String(text).trim().toLowerCase();
  const status = sane ? "PASS" : "FAIL";
  console.log(JSON.stringify({ translate_single: out }));
  console.log(JSON.stringify({ check: "hf_translate_single", status, src, tgt, ms }));
  return { status, out };
}

async function main() {
  const opts = parseArgs(process.argv);
  const t0 = Date.now();
  const healthy = await waitForHealth(HEALTH_URL, 10_000, 500);
  console.log(JSON.stringify({ health: healthy ? "PASS" : "FAIL", url: HF_URL, healthUrl: HEALTH_URL }));
  if (!healthy) {
    throw new Error(`Health check failed for ${HEALTH_URL}`);
  }

  const tInit0 = Date.now();
  const client = await withTimeout(connectClient(), INIT_TIMEOUT_MS, "initialize");
  const tInit = Date.now() - tInit0;

  try {
    const { tools } = await withTimeout(client.listTools(), CALL_TIMEOUT_MS, "listTools");
    const names = toolNames(tools);
    console.log(JSON.stringify({ url: HF_URL }));
    console.log(JSON.stringify({ tools: names }));

    const summary = { status: "PASS", tInit };

    // Focused check mode for CI or targeted probes
    if (opts.check === "hf_translate_single") {
      if (!names.includes("hf_translate")) {
        console.log(JSON.stringify({ check: "hf_translate_single", status: "FAIL", reason: "hf_translate not available" }));
        throw new Error("hf_translate tool not available");
      }
      const result = await runTranslateSingle(client);
      if (result.status !== "PASS") {
        // Fail fast in targeted mode
        throw new Error("hf_translate_single failed");
      }
      const totalMs = Date.now() - t0;
      console.log(JSON.stringify({ summary: { status: "PASS", totalMs } }));
      return;
    }

    // 1) Sentiment: sample + batch + override
    if (names.includes("hf_sentiment")) {
      const tSample0 = Date.now();
      const sampleRaw = await withTimeout(
        client.callTool({ name: "hf_sentiment", arguments: { text: "I absolutely love this project!" } }),
        CALL_TIMEOUT_MS,
        "call hf_sentiment (sample)"
      );
      const sample = extractPayload(sampleRaw);
      const tSample = Date.now() - tSample0;
      summary.tSentSample = tSample;
      console.log(JSON.stringify({ sentiment_sample: sample }));

      const tBatch0 = Date.now();
      const batchRaw = await withTimeout(
        client.callTool({ name: "hf_sentiment", arguments: { text: ["great job", "this is awful"] } }),
        CALL_TIMEOUT_MS,
        "call hf_sentiment (batch)"
      );
      const batch = extractPayload(batchRaw);
      const tBatch = Date.now() - tBatch0;
      summary.tSentBatch = tBatch;
      console.log(JSON.stringify({ sentiment_batch: batch }));

      const tOverride0 = Date.now();
      const overrideRaw = await withTimeout(
        client.callTool({
          name: "hf_sentiment",
          arguments: {
            text: ["great job", "this is awful"],
            model_id: process.env.HF_SENT_OVERRIDE || "distilbert-base-uncased-finetuned-sst-2-english",
          },
        }),
        CALL_TIMEOUT_MS,
        "call hf_sentiment (override)"
      );
      const override = extractPayload(overrideRaw);
      const tOverride = Date.now() - tOverride0;
      summary.tSentOverride = tOverride;
      console.log(JSON.stringify({ sentiment_override: override }));
    }

  // 2) Embeddings: batch
    if (names.includes("hf_embeddings")) {
      const embedTexts = ["hello world", "how are you"];
      const tEmb0 = Date.now();
  const embRaw = await withTimeout(
        client.callTool({ name: "hf_embeddings", arguments: { text: embedTexts } }),
        CALL_TIMEOUT_MS,
        "call hf_embeddings"
      );
      // Debug raw envelope if needed
      try {
        let dbg;
        if (Array.isArray(embRaw?.content)) {
          dbg = embRaw.content.map((c) => ({
            type: c?.type,
            textLen: c?.text?.length,
            hasJson: c?.json !== undefined,
            textHead: typeof c?.text === "string" ? c.text.slice(0, 200) : undefined,
          }));
        }
        console.log(JSON.stringify({ embeddings_envelope: dbg }));
      } catch {}

      let emb = extractPayload(embRaw);
      if (emb && typeof emb === "object" && !Array.isArray(emb) && emb.error === "unavailable") {
        // Offline/no-cache or deps missing: treat as SKIP (local-only posture still validated)
        const tEmb = Date.now() - tEmb0;
        summary.tEmbeddings = tEmb;
        console.log(JSON.stringify({ embeddings: { status: "SKIP", reason: emb.message } }));
        // Do not throw; continue to next checks
      } else {
      if (typeof emb === "string") {
        try {
          // Try joining raw text parts explicitly if extract didn't
          if (Array.isArray(embRaw?.content)) {
            const joined = embRaw.content
              .map((c) => (typeof c?.text === "string" ? c.text : ""))
              .join("");
            emb = JSON.parse(joined);
          }
        } catch {}
      }
      if (typeof emb === "string") {
        try {
          const parsed = JSON.parse(emb);
          emb = parsed;
        } catch {}
      }
      const tEmb = Date.now() - tEmb0;
      summary.tEmbeddings = tEmb;
      let dims = [];
      let ok = false;
      if (Array.isArray(emb)) {
        if (emb.length && emb.every((x) => typeof x === "number")) {
          dims = [emb.length];
          ok = dims[0] > 0; // single vector
        } else if (emb.length && emb.every((x) => Array.isArray(x))) {
          dims = emb.map((v) => (Array.isArray(v) ? v.length : 0));
          ok = dims.every((d) => d > 0) && (dims.length === embedTexts.length || dims.length === 1);
        } else {
          dims = emb.map((v) => (Array.isArray(v) ? v.length : 0));
        }
      }
      console.log(JSON.stringify({ embeddings: { dims } }));
      if (!ok) {
        try {
          console.log(
            JSON.stringify({
              embeddings_debug: {
                embType: Array.isArray(emb) ? "array" : typeof emb,
                embKeys: emb && typeof emb === "object" ? Object.keys(emb) : undefined,
              },
            })
          );
        } catch {}
        throw new Error("hf_embeddings validation failed");
      }
      }
    }

    // 3) Summarize: single + batch
    if (names.includes("hf_summarize")) {
      const text =
        "Streamlit is a fast way to build data apps. It lets you turn Python scripts into shareable web apps in minutes.";
      const tSum0 = Date.now();
      const sumRaw = await withTimeout(
        client.callTool({ name: "hf_summarize", arguments: { text, max_new_tokens: 40, min_new_tokens: 5 } }),
        CALL_TIMEOUT_MS,
        "call hf_summarize (single)"
      );
      const sum = extractPayload(sumRaw);
      const tSum = Date.now() - tSum0;
      summary.tSummarize = tSum;
      if (sum && typeof sum === "object" && sum.error === "unavailable") {
        console.log(JSON.stringify({ summarize_single: { status: "SKIP", reason: sum.message } }));
      } else {
        console.log(JSON.stringify({ summarize_single: sum }));
        if (typeof sum !== "string" || sum.length === 0) throw new Error("hf_summarize single returned empty");
        console.log(JSON.stringify({ check: "hf_summarize_single", status: "PASS", ms: tSum }));
      }

      const tSumB0 = Date.now();
      const sumBRaw = await withTimeout(
        client.callTool({ name: "hf_summarize", arguments: { text: [text, text], max_new_tokens: 40, min_new_tokens: 5 } }),
        CALL_TIMEOUT_MS,
        "call hf_summarize (batch)"
      );
      const sumB = extractPayload(sumBRaw);
      const tSumB = Date.now() - tSumB0;
      summary.tSummarizeBatch = tSumB;
      if (sumB && typeof sumB === "object" && !Array.isArray(sumB) && sumB.error === "unavailable") {
        console.log(JSON.stringify({ summarize_batch: { status: "SKIP", reason: sumB.message } }));
      } else {
        console.log(JSON.stringify({ summarize_batch: sumB }));
        if (!Array.isArray(sumB) || sumB.length !== 2 || sumB.some((s) => typeof s !== "string" || s.length === 0)) {
          throw new Error("hf_summarize batch returned invalid output");
        }
        console.log(JSON.stringify({ check: "hf_summarize_batch", status: "PASS", ms: tSumB }));
      }
    }

    // 4) Zero-shot: single + batch
    if (names.includes("hf_zero_shot")) {
      const labels = ["food", "sports", "politics"];
  const zsDebug = process.env.HF_ZS_DEBUG === "1";
      const tZs0 = Date.now();
  const zsRaw = await withTimeout(
    client.callTool({ name: "hf_zero_shot", arguments: { text: "I love pizza", labels, multi_label: false, debug: zsDebug } }),
        20_000,
        "call hf_zero_shot (single)"
      );
  const zs = extractPayload(zsRaw);
      const tZs = Date.now() - tZs0;
      summary.tZeroShot = tZs;
      if (zs && typeof zs === "object" && zs.error === "unavailable") {
        console.log(JSON.stringify({ zero_shot_single: { status: "SKIP", reason: zs.message } }));
      } else {
        console.log(JSON.stringify({ zero_shot_single: zs }));
        const sOk = zs && typeof zs === "object" && labels.includes(zs.label) && zs.score >= 0 && zs.score <= 1;
        if (!sOk) throw new Error("hf_zero_shot single validation failed");
        console.log(JSON.stringify({ check: "hf_zero_shot_single", status: "PASS", ms: tZs }));
      }

      const tZsB0 = Date.now();
  const zsBRaw = await withTimeout(
        client.callTool({ name: "hf_zero_shot", arguments: { text: ["I love pizza", "The match was intense"], labels, multi_label: false, debug: zsDebug } }),
        20_000,
        "call hf_zero_shot (batch)"
      );
      const zsB = extractPayload(zsBRaw);
      const tZsB = Date.now() - tZsB0;
      summary.tZeroShotBatch = tZsB;
      if (zsB && typeof zsB === "object" && !Array.isArray(zsB) && zsB.error === "unavailable") {
        console.log(JSON.stringify({ zero_shot_batch: { status: "SKIP", reason: zsB.message } }));
      } else {
        console.log(JSON.stringify({ zero_shot_batch: zsB }));
        const bOk = Array.isArray(zsB) && zsB.length === 2 && zsB.every((o) => labels.includes(o.label));
        if (!bOk) throw new Error("hf_zero_shot batch validation failed");
        console.log(JSON.stringify({ check: "hf_zero_shot_batch", status: "PASS", ms: tZsB }));
      }
    }

    // 5) Generate: single + batch
    if (names.includes("hf_generate")) {
      // single
      const tGS0 = Date.now();
      const genRaw = await withTimeout(
        client.callTool({ name: "hf_generate", arguments: { text: "The quick brown fox", max_new_tokens: 32, temperature: 0.7, top_p: 0.9 } }),
        CALL_TIMEOUT_MS,
        "call hf_generate (single)"
      );
      const gen = extractPayload(genRaw);
      const tGS = Date.now() - tGS0;
      summary.tGenerateSingle = tGS;
      if (gen && typeof gen === "object" && gen.error === "unavailable") {
        console.log(JSON.stringify({ generate_single: { status: "SKIP", reason: gen.message } }));
      } else {
        console.log(JSON.stringify({ generate_single: gen }));
        if (typeof gen !== "string" || gen.length === 0) throw new Error("hf_generate single returned empty");
        console.log(JSON.stringify({ check: "hf_generate_single", status: "PASS", ms: tGS }));
      }

      // batch
      const prompts = ["Once upon a time", "In a galaxy far away"];
      const tGB0 = Date.now();
      const genBRaw = await withTimeout(
        client.callTool({ name: "hf_generate", arguments: { text: prompts, max_new_tokens: 32 } }),
        CALL_TIMEOUT_MS,
        "call hf_generate (batch)"
      );
      const genB = extractPayload(genBRaw);
      const tGB = Date.now() - tGB0;
      summary.tGenerateBatch = tGB;
      if (genB && typeof genB === "object" && !Array.isArray(genB) && genB.error === "unavailable") {
        console.log(JSON.stringify({ generate_batch: { status: "SKIP", reason: genB.message } }));
      } else {
        console.log(JSON.stringify({ generate_batch: genB }));
        if (!Array.isArray(genB) || genB.length !== 2 || genB.some((s) => typeof s !== "string" || s.length === 0)) {
          throw new Error("hf_generate batch returned invalid output");
        }
        console.log(JSON.stringify({ check: "hf_generate_batch", status: "PASS", ms: tGB }));
      }
    }

    // 6) Translate: single + batch
    if (names.includes("hf_translate")) {
      // single en->es
      const tTS0 = Date.now();
      const txSRaw = await withTimeout(
        client.callTool({ name: "hf_translate", arguments: { text: "hello world", src: "en", tgt: "es" } }),
        CALL_TIMEOUT_MS,
        "call hf_translate (single)"
      );
      const txS = extractPayload(txSRaw);
      const tTS = Date.now() - tTS0;
      summary.tTranslateSingle = tTS;
      console.log(JSON.stringify({ translate_single: txS }));
      let txUnavailable = false;
      if (txS && typeof txS === "object" && txS.error === "unavailable") {
        // Graceful skip when server reports unavailable (e.g., torch<2.6 restriction for torch.load)
        console.log(JSON.stringify({ check: "hf_translate_single", status: "SKIP", ms: tTS, reason: txS.message }));
        summary.translateSingle = "SKIP";
        txUnavailable = true;
      } else {
        if (typeof txS !== "string" || txS.length === 0) throw new Error("hf_translate single returned empty");
        console.log(JSON.stringify({ check: "hf_translate_single", status: "PASS", ms: tTS }));
      }

      // batch en->fr
      if (txUnavailable) {
        // If single is unavailable, skip batch proactively to avoid timeouts
        console.log(
          JSON.stringify({
            check: "hf_translate_batch",
            status: "SKIP",
            ms: 0,
            reason: "skipped because single reported unavailable",
          })
        );
        summary.translateBatch = "SKIP";
      } else {
        const tTB0 = Date.now();
        const batchTexts = ["how are you?", "nice to meet you"];
        const txBRaw = await withTimeout(
          client.callTool({ name: "hf_translate", arguments: { text: batchTexts, src: "en", tgt: "fr" } }),
          CALL_TIMEOUT_MS,
          "call hf_translate (batch)"
        );
        const txB = extractPayload(txBRaw);
        const tTB = Date.now() - tTB0;
        summary.tTranslateBatch = tTB;
        console.log(JSON.stringify({ translate_batch: txB }));
        if (txB && typeof txB === "object" && !Array.isArray(txB) && txB.error === "unavailable") {
          console.log(JSON.stringify({ check: "hf_translate_batch", status: "SKIP", ms: tTB, reason: txB.message }));
          summary.translateBatch = "SKIP";
        } else {
          if (!Array.isArray(txB) || txB.length !== 2 || txB.some((s) => typeof s !== "string" || s.length === 0)) {
            throw new Error("hf_translate batch returned invalid output");
          }
          console.log(JSON.stringify({ check: "hf_translate_batch", status: "PASS", ms: tTB }));
        }
      }
    }

    const totalMs = Date.now() - t0;
    console.log(JSON.stringify({ summary: { ...summary, totalMs } }));
  } finally {
    try {
      await client.close?.();
    } catch {}
  }
}

main().then(() => process.exit(0)).catch((e) => {
  const msg = e?.message || String(e);
  console.error(JSON.stringify({ summary: { status: "FAIL", error: msg } }));
  process.exit(1);
});
