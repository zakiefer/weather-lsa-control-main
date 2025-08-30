#!/usr/bin/env python3
"""
Verify Apify MCP HTTP configuration without making network calls.

Checks:
- .vscode/mcp.json contains an "apify" server with type "http" and a URL
- Authorization header resolves from ${env:APIFY_TOKEN}
- .env.local provides a non-empty APIFY_TOKEN and isn't the placeholder

Outputs a redacted JSON summary so you can safely share the result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_JSON = ROOT / ".vscode" / "mcp.json"
ENV_LOCAL = ROOT / ".env.local"


def load_env_local(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("```"):
            # ignore fenced code markers
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        # only capture APIFY_TOKEN to avoid loading unrelated secrets
        if k == "APIFY_TOKEN":
            env[k] = v
    return env


def _strip_jsonc(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    in_str = False
    str_quote = ""
    escape = False
    while i < n:
        ch = s[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_quote:
                in_str = False
            i += 1
            continue
        # not in string
        if ch == '"' or ch == "'":
            in_str = True
            str_quote = ch
            out.append(ch)
            i += 1
            continue
        # line comment //
        if ch == "/" and i + 1 < n and s[i + 1] == "/":
            # skip to end of line
            while i < n and s[i] != "\n":
                i += 1
            continue
        # block comment /* ... */
        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_jsonc(path: Path) -> dict:
    if not path.exists():
        return {}
    s = path.read_text(encoding="utf-8")
    s = _strip_jsonc(s)
    try:
        return json.loads(s)
    except Exception:
        return {}


def redact(token: str | None) -> str:
    if not token:
        return ""
    # show only last 4 chars
    if len(token) <= 8:
        return "*" * (len(token) - 4) + token[-4:]
    return ("*" * (len(token) - 4)) + token[-4:]


def main() -> int:
    env = load_env_local(ENV_LOCAL)
    apify_token = env.get("APIFY_TOKEN", "")

    cfg = load_jsonc(MCP_JSON)
    servers = cfg.get("servers") or {}
    apify = servers.get("apify") if isinstance(servers, dict) else None

    result = {
        "files": {
            "mcp_json": str(MCP_JSON),
            "env_local": str(ENV_LOCAL),
        },
        "apify_mcp_present": bool(apify),
        "apify_mcp_type": apify.get("type") if isinstance(apify, dict) else None,
        "apify_url": apify.get("url") if isinstance(apify, dict) else None,
        "authorization_header_configured": False,
        "authorization_header_resolves": False,
        "apify_token_present": bool(apify_token),
        "apify_token_placeholder": apify_token.strip().upper().startswith("YOUR_") if apify_token else None,
        "apify_token_redacted": redact(apify_token) if apify_token else "",
        "notes": [],
    }

    headers = apify.get("headers") if isinstance(apify, dict) else None
    if isinstance(headers, dict):
        auth = headers.get("Authorization")
        if isinstance(auth, str):
            result["authorization_header_configured"] = True
            # Resolve ${env:APIFY_TOKEN}
            m = re.match(r"^Bearer\s+\$\{env:APIFY_TOKEN\}$", auth)
            if m:
                result["authorization_header_resolves"] = bool(apify_token)
            else:
                # If it's a literal token (not recommended), redact
                if auth.startswith("Bearer "):
                    literal = auth.split(" ", 1)[1]
                    result["authorization_header_resolves"] = True
                    result["apify_token_present"] = True
                    result["apify_token_placeholder"] = False
                    result["apify_token_redacted"] = redact(literal)
                    result["notes"].append("Authorization header contains a literal token; prefer ${env:APIFY_TOKEN}.")

    if not result["apify_mcp_present"]:
        result["notes"].append("No 'apify' server found in .vscode/mcp.json.")
    if result["apify_mcp_type"] != "http":
        result["notes"].append("'apify' server is not configured as type=http.")
    if not result["apify_url"]:
        result["notes"].append("Missing Apify MCP URL.")
    if not result["authorization_header_configured"]:
        result["notes"].append("Authorization header not configured.")
    if not result["authorization_header_resolves"]:
        result["notes"].append(
            "Authorization bearer token not resolvable; set APIFY_TOKEN in .env.local and reload VS Code."
        )
    if result["apify_token_placeholder"]:
        result["notes"].append("APIFY_TOKEN appears to be a placeholder. Replace with your real token.")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
