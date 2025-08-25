import fetch from "node-fetch";

// Minimal MCP-like process: expose simple stdio JSON-RPC if desired.
// For this task, implement a tiny HTTP-bridging server process that supports
// two simple actions via stdin commands in the future if needed. For now,
// we just start and log readiness; an MCP host will invoke it and communicate
// using its protocol expectations. We'll focus on OpenRouter calls.

const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY || "";
const OPENROUTER_URL = "https://openrouter.ai/api/v1";

if (!OPENROUTER_API_KEY) {
  console.error("OPENROUTER_API_KEY is not set");
}

// Supported models
const MODELS = [
  "openai/gpt-4o",
  "anthropic/claude-3-5-sonnet-latest",
  "google/gemini-1.5-pro-latest",
  "meta-llama/llama-3.1-405b-instruct",
];

function pickBest(responses: string[]): string {
  // Naive consensus: pick the longest non-empty response
  const clean = responses.map((s) => (s || "").trim()).filter(Boolean);
  if (clean.length === 0) return "";
  clean.sort((a, b) => b.length - a.length);
  return clean[0];
}

async function chat(model: string, messages: any[]): Promise<string> {
  const url = `${OPENROUTER_URL}/chat/completions`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${OPENROUTER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model, messages }),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`OpenRouter error ${r.status}: ${text}`);
  }
  const data: any = await r.json();
  return data?.choices?.[0]?.message?.content ?? "";
}

async function handleListModels() {
  return MODELS;
}

async function handleChat(model: string, messages: any[]): Promise<string> {
  if (model === "best_ensemble") {
    const targets = [
  "openai/gpt-4o",
      "anthropic/claude-3-5-sonnet-latest",
      "google/gemini-1.5-pro-latest",
    ];
    const results = await Promise.allSettled(targets.map((m) => chat(m, messages)));
    const texts = results.map((res) => (res.status === "fulfilled" ? res.value : ""));
    return pickBest(texts);
  }
  return chat(model, messages);
}

// Minimal stdin command handler for quick manual smoke if needed
process.stdin.setEncoding("utf8");
console.log(JSON.stringify({ ready: true, models: MODELS.slice(0, 4) }));
let buf = "";
// Ensure the process remains alive even if no stdin data arrives
process.stdin.resume();
// Ultra-light keepalive to keep event loop active across nohup/background runs
setInterval(() => {}, 60 * 1000);
process.stdin.on("data", (chunk: string) => {
  buf += chunk;
  if (buf.indexOf("\n") !== -1) {
    const lines = buf.split("\n");
    buf = lines.pop() || "";
    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      try {
        const req = JSON.parse(t);
        if (req.type === "list-models") {
          handleListModels().then((models) =>
            console.log(JSON.stringify({ id: req.id, ok: true, models }))
          ).catch((e) => console.log(JSON.stringify({ id: req.id, ok: false, error: String(e) })));
        } else if (req.type === "chat") {
          handleChat(req.model || MODELS[0], req.messages || [])
            .then((text) => console.log(JSON.stringify({ id: req.id, ok: true, text })))
            .catch((e) => console.log(JSON.stringify({ id: req.id, ok: false, error: String(e) })));
        } else {
          console.log(JSON.stringify({ id: req.id, ok: false, error: "unknown type" }));
        }
      } catch (e) {
        console.log(JSON.stringify({ ok: false, error: String(e) }));
      }
    }
  }
});
