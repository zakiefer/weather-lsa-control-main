#!/usr/bin/env python3
"""
Discover top LLMs via OpenRouter (optional), select best per vendor, and patch .mcp.json:
- Vendors: openai, anthropic, google, meta, optional: cohere (if reasoning tier)
- Exclude low-tier: mini|small|lite|flash|preview-blocked
- Prefer tags: reasoning, latest; prefer larger context
- Create aliases: best_openai, best_anthropic, best_google, best_meta, best_code, best_long, best_vision, best_ensemble
- Update allowed/default across MCP servers (model keys), keeping defaults top-tier

Requires env OPENROUTER_API_KEY (optional; will skip discovery if missing).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import typing as t
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_JSON = ROOT / ".mcp.json"
BAK = ROOT / ".mcp.json.bak"

LOW_TIER_RE = re.compile(
    r"mini|small|lite|flash|preview-blocked|gemma|mistral-7b|(?:^|-)4b(?:-|$)|(?:^|-)7b(?:-|$)|(?:^|-)8b(?:-|$)|(?:^|-)13b(?:-|$)",
    re.I,
)


def fetch_openrouter_models(api_key: str | None) -> list[dict[str, t.Any]]:
    url = "https://openrouter.ai/api/v1/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec - endpoint fixed
            data = json.load(resp)
        return data.get("data", [])
    except Exception as e:  # pragma: no cover
        print(f"WARN: unable to list models from OpenRouter: {e}", file=sys.stderr)
        return []


def vendor_of(model_id: str) -> str:
    if model_id.startswith("openai/"):
        return "openai"
    if model_id.startswith("anthropic/"):
        return "anthropic"
    if model_id.startswith("google/") or model_id.startswith("google_ai/"):
        return "google"
    if model_id.startswith("meta/") or model_id.startswith("meta-llama/"):
        return "meta"
    if model_id.startswith("cohere/"):
        return "cohere"
    return "other"


def score_model(m: dict[str, t.Any]) -> tuple[int, int, int]:
    """Score with vendor-aware boosts to enforce top-tier preference."""
    mid = (m.get("id") or m.get("name") or "").lower()
    vend = vendor_of(mid)
    tags = set(m.get("tags") or [])
    tag_score = 0
    for tag in ("reasoning", "latest"):
        if tag in tags:
            tag_score += 10
    boost = 0
    if vend == "openai":
        if "gpt-5" in mid or "/o4" in mid:
            boost = 1_000_000
        elif "gpt-4.1" in mid:
            boost = 100_000
    elif vend == "anthropic":
        if "claude-3-5-sonnet" in mid:
            boost = 1_000_000
    elif vend == "google":
        if "gemini-1.5-pro" in mid and "flash" not in mid:
            boost = 1_000_000
    elif vend == "meta":
        if "llama-3.1-405b-instruct" in mid:
            boost = 1_000_000
    ctx = 0
    try:
        ctx = int(m.get("context_length", 0))
    except Exception:
        ctx = 0
    return boost, tag_score, ctx


def pick_best_per_vendor(models: list[dict[str, t.Any]]) -> dict[str, dict[str, t.Any]]:
    best: dict[str, dict[str, t.Any]] = {}
    for m in models:
        mid = m.get("id") or m.get("name") or ""
        if not mid or LOW_TIER_RE.search(mid):
            continue
        vend = vendor_of(mid)
        if vend not in {"openai", "anthropic", "google", "meta", "cohere"}:
            continue
        s = score_model(m)
        cur = best.get(vend)
        if cur is None or score_model(cur) < s:
            best[vend] = m | {"id": mid}
    # optional: drop cohere if nothing picked
    if "cohere" in best and not best["cohere"].get("tags"):
        best.pop("cohere", None)
    return best


def enforce_minimums(best: dict[str, dict[str, t.Any]]) -> dict[str, dict[str, t.Any]]:
    """Ensure each vendor points to a top-tier model; fallback to curated choices if needed."""
    curated: dict[str, str] = {
        "openai": "openai/gpt-4o",
        "anthropic": "anthropic/claude-3-5-sonnet-latest",
        "google": "google/gemini-1.5-pro-latest",
        "meta": "meta-llama/llama-3.1-405b-instruct",
    }
    b = dict(best)

    # Normalize any vendor IDs to drop variant suffixes like ":free"
    def norm(mid: str) -> str:
        return mid.split(":", 1)[0]

    # OpenAI: allow gpt-4o, gpt-5-preview, o4, o3, gpt-4.1; otherwise fallback to curated
    if "openai" in b:
        oid = norm(b["openai"].get("id", ""))
        if not any(s in oid for s in ("gpt-4o", "gpt-5-preview", "o4", "o3", "gpt-4.1")):
            b["openai"] = {"id": curated["openai"], "tags": ["latest", "reasoning"], "context_length": 200000}
        else:
            b["openai"]["id"] = oid
    else:
        b["openai"] = {"id": curated["openai"], "tags": ["latest", "reasoning"], "context_length": 200000}
    # Anthropic: prefer 3.5 Sonnet latest
    if "anthropic" in b:
        aid = norm(b["anthropic"].get("id", ""))
        if "claude-3-5-sonnet" not in aid:
            b["anthropic"] = {"id": curated["anthropic"], "tags": ["latest", "reasoning"], "context_length": 200000}
        else:
            b["anthropic"]["id"] = aid
    # Google: must be gemini 1.5 pro (not gemma or flash)
    if "google" in b:
        gid = norm(b["google"].get("id", ""))
        if ("gemini" not in gid) or ("pro" not in gid) or ("flash" in gid):
            b["google"] = {"id": curated["google"], "tags": ["latest", "multimodal"], "context_length": 1000000}
        else:
            b["google"]["id"] = gid
    else:
        b["google"] = {"id": curated["google"], "tags": ["latest", "multimodal"], "context_length": 1000000}
    # Meta: prefer 3.1 405b instruct
    if "meta" in b:
        mid = norm(b["meta"].get("id", ""))
        if "405b" not in mid:
            b["meta"] = {"id": curated["meta"], "tags": ["latest"], "context_length": 128000}
        else:
            b["meta"]["id"] = mid
    return b


def compute_aliases(best: dict[str, dict[str, t.Any]]) -> dict[str, list[str]]:
    ids = {k: v["id"] for k, v in best.items()}

    # best_long: largest context among available
    def ctx(vendor: str) -> int:
        try:
            return int(best[vendor].get("context_length", 0))
        except Exception:
            return 0

    longest_vendor = max(ids.keys(), key=lambda v: ctx(v), default="openai")

    # vision: prefer openai then google if tags include vision/image
    def vision_capable(vendor: str) -> bool:
        tags = set(best.get(vendor, {}).get("tags") or [])
        return any(tag in tags for tag in ("vision", "multimodal", "image"))

    best_vision = None
    for cand in ["openai", "google", "anthropic", "meta"]:
        if cand in ids and vision_capable(cand):
            best_vision = ids[cand]
            break
    aliases: dict[str, list[str]] = {}
    if "openai" in ids:
        aliases["best_openai"] = [ids["openai"]]
    if "anthropic" in ids:
        aliases["best_anthropic"] = [ids["anthropic"]]
    if "google" in ids:
        aliases["best_google"] = [ids["google"]]
    if "meta" in ids:
        aliases["best_meta"] = [ids["meta"]]
    # code: prefer vendor with stronger code tools (heuristic: openai then anthropic)
    if "openai" in ids:
        aliases["best_code"] = [ids["openai"]]
    elif "anthropic" in ids:
        aliases["best_code"] = [ids["anthropic"]]
    # long
    if longest_vendor in ids:
        aliases["best_long"] = [ids[longest_vendor]]
    # vision
    if best_vision:
        aliases["best_vision"] = [best_vision]
    # ensemble
    ens = [ids[v] for v in ["openai", "anthropic", "google"] if v in ids]
    if ens:
        aliases["best_ensemble"] = ens
    return aliases


def load_json(p: Path) -> dict[str, t.Any]:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(p: Path, obj: dict[str, t.Any]):
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def patch_mcp(
    mcp: dict[str, t.Any],
    aliases: dict[str, list[str]],
    allow_models: list[str],
    default_model: str | None,
) -> tuple[dict[str, t.Any], dict[str, str]]:
    """Update model keys across servers; return previous/new defaults per server."""
    servers = mcp.get("servers") or {}
    changes: dict[str, str] = {}
    for name, cfg in servers.items():
        models = cfg.get("models")
        prev_default = None
        if isinstance(models, dict):
            prev_default = models.get("default") or models.get("defaultModel")
            # Router and OpenRouter use OpenRouter-style IDs; set allow exactly
            if name in {"llm-router", "openrouter"}:
                new_allow = sorted(set(allow_models))
                if models.get("allow") != new_allow:
                    models["allow"] = new_allow
                if default_model and (
                    models.get("default") != default_model or models.get("defaultModel") != default_model
                ):
                    models["default"] = default_model
                    models["defaultModel"] = default_model
                if name == "llm-router":
                    models.setdefault("aliases", {}).update(aliases)
                    routing = models.setdefault("routing", {})
                    routing.update(
                        {
                            "chat": "best_ensemble",
                            "planning": "best_ensemble",
                            "refactor": "best_ensemble",
                            "codegen": "best_code",
                            "long_context": "best_long",
                            "vision": "best_vision",
                            "fallback": "best_openai",
                        }
                    )
                    # prefer OpenAI by default if available
                    models["defaultAlias"] = "best_openai"
            elif name == "openai":
                # Map vendor IDs to OpenAI bare IDs
                allowed_openai = [mid.split("/", 1)[1] for mid in allow_models if mid.startswith("openai/")]
                if allowed_openai:
                    new_allow = sorted(set(allowed_openai))
                    if models.get("allow") != new_allow:
                        models["allow"] = new_allow
                if default_model and default_model.startswith("openai/"):
                    dm = default_model.split("/", 1)[1]
                    if models.get("default") != dm or models.get("defaultModel") != dm:
                        models["default"] = dm
                        models["defaultModel"] = dm
            # record change only if default changed
            new_default = models.get("default") or models.get("defaultModel")
            if prev_default != new_default:
                changes[name] = f"{prev_default or '-'} -> {new_default or '-'}"
    return mcp, changes


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover top models and patch .mcp.json")
    p.add_argument("--dry-run", action="store_true", help="Only print the plan; do not write")
    return p.parse_args()


def env_truthy(name: str, default: str = "1") -> bool:
    val = os.environ.get(name, default)
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    args = parse_args()
    strict = env_truthy("MCP_MODELS_STRICT", "1")

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    models = fetch_openrouter_models(openrouter_key)
    if not models:
        print("WARN: Skipping dynamic discovery (no models fetched). I will only persist structure.")
    best = pick_best_per_vendor(models)
    if not best and not strict:
        # Static fallbacks (top-tier, no mini/lite/flash) — only in non-strict mode
        best = {
            "openai": {
                "id": "openai/gpt-4o",
                "tags": ["latest", "reasoning"],
                "context_length": 200000,
            },
            "anthropic": {
                "id": "anthropic/claude-3-5-sonnet-latest",
                "tags": ["latest", "reasoning"],
                "context_length": 200000,
            },
            "google": {
                "id": "google/gemini-1.5-pro-latest",
                "tags": ["latest", "multimodal"],
                "context_length": 1000000,
            },
            "meta": {
                "id": "meta-llama/llama-3.1-405b-instruct",
                "tags": ["latest"],
                "context_length": 128000,
            },
        }
    elif best:
        # Ensure top-tier choices even if discovery returned smaller variants
        best = enforce_minimums(best)

    aliases = compute_aliases(best) if best else {}
    default_model = aliases.get("best_openai", [None])[0]
    allow_models = sorted({mid for arr in aliases.values() for mid in arr}) if aliases else []

    # Strict gating: require OpenAI GPT-5 or o4 to be visible as best
    if strict:
        if not default_model or not re.search(r"^openai/(gpt-5|o4|gpt-4o)", default_model):
            print("No GPT-5 visible to this key/provider. No changes written.", file=sys.stderr)
            return 3

    if args.dry_run:
        print(
            json.dumps(
                {
                    "backup": None,
                    "aliases": aliases,
                    "default": default_model,
                    "allow": allow_models,
                    "changes": {},
                    "dry_run": True,
                    "strict": strict,
                },
                indent=2,
            )
        )
        return 0

    # Write mode
    if not MCP_JSON.exists():
        print(f"ERR: {MCP_JSON} not found", file=sys.stderr)
        return 2
    bak_written = False
    try:
        if MCP_JSON.exists():
            BAK.write_text(MCP_JSON.read_text(encoding="utf-8"), encoding="utf-8")
            bak_written = True
    except Exception:
        pass
    mcp = load_json(MCP_JSON)
    mcp, changes = patch_mcp(mcp, aliases, allow_models, default_model)
    save_json(MCP_JSON, mcp)
    print(
        json.dumps(
            {
                "backup": str(BAK) if bak_written else None,
                "aliases": aliases,
                "default": default_model,
                "allow": allow_models,
                "changes": changes,
                "dry_run": False,
                "strict": strict,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
