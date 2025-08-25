"""
Auto-start all locally configured MCP servers.

Contract
- Input: Optional flags to filter which servers to start, strict exit on failures, and timeout.
- Reads: .mcp.json from repo root (next to this tools/ directory).
- Behavior: For each server with a startable command, spawn process with resolved cwd/env, wait for basic readiness
  (process alive for a grace period or health check), capture logs, and print a concise summary.
- Output: Exit code 0 when at least one server started and no start failures
    (unless --strict). Non-zero on failures in strict mode.

Notes
- We intentionally skip servers of type "builtin", "commands", and entries without a runnable command.
- Placeholder resolution supported: ${workspaceFolder}, ${env:VAR}.

Example
  python3 tools/mcp_start_all.py --strict --timeout 12
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import time
import typing as t

ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_CONFIG_PATH = ROOT / ".mcp.json"
LOG_DIR = ROOT / "logs" / "mcp"


def _resolve_placeholder(val: str) -> str:
    if "${workspaceFolder}" in val:
        val = val.replace("${workspaceFolder}", str(ROOT))
    # Simple ${env:VAR} expansion
    # Supports multiple expansions within the same string
    out = ""
    i = 0
    while i < len(val):
        if val.startswith("${env:", i):
            j = val.find("}", i)
            if j != -1:
                key = val[i + len("${env:") : j]
                out += os.environ.get(key, "")
                i = j + 1
                continue
        out += val[i]
        i += 1
    return out


def _resolve_env(env_spec: t.Any) -> dict[str, str]:
    merged = dict(os.environ)
    if env_spec is None:
        return merged
    if isinstance(env_spec, dict):
        for k, v in env_spec.items():
            if isinstance(v, str):
                merged[k] = _resolve_placeholder(v)
            else:
                merged[k] = str(v)
    elif isinstance(env_spec, list):
        # A list of required env var names; we don't set values, just ensure presence.
        for name in env_spec:
            if name not in merged:
                # Leave missing; caller can decide whether to warn.
                pass
    return merged


def _startable(server_cfg: dict[str, t.Any]) -> bool:
    if not isinstance(server_cfg, dict):
        return False
    if server_cfg.get("type") in {"builtin", "commands"}:
        return False
    # Startable when explicit command present
    return bool(server_cfg.get("command"))


def _build_command(server_cfg: dict[str, t.Any]) -> tuple[list[str], str | None]:
    cmd = server_cfg.get("command")
    args = server_cfg.get("args") or []
    cwd = server_cfg.get("cwd")
    if isinstance(cwd, str):
        cwd = _resolve_placeholder(cwd)
    if isinstance(cmd, list):
        base = [str(x) for x in cmd]
    elif isinstance(cmd, str):
        if args:
            base = [cmd]
        else:
            # Split when args not provided
            base = shlex.split(cmd)
    else:
        raise ValueError("Invalid command specification")
    # Resolve placeholders in args
    resolved_args = [(_resolve_placeholder(a) if isinstance(a, str) else str(a)) for a in args]
    return base + resolved_args, cwd


def _open_logs(name: str) -> tuple[t.Any, t.Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = LOG_DIR / f"{name}.out.log"
    stderr_path = LOG_DIR / f"{name}.err.log"
    stdout = open(stdout_path, "ab", buffering=0)
    stderr = open(stderr_path, "ab", buffering=0)
    return stdout, stderr


def _wait_readiness(proc: subprocess.Popen, timeout: float) -> bool:
    # Basic readiness: process stays alive for the grace period.
    # Poll until timeout; if it exits early, consider failed.
    start = time.time()
    while time.time() - start < timeout:
        rc = proc.poll()
        if rc is not None:
            return False
        time.sleep(0.25)
    return proc.poll() is None


def start_servers(
    servers: dict[str, dict[str, t.Any]],
    only: list[str] | None = None,
    timeout: float = 8.0,
) -> dict[str, t.Any]:
    def build_start_order() -> list[str]:
        """Return a deterministic, dependency-aware startup order.

        Respects an optional 'dependsOn' list per server config.
        Filters to 'only' if provided.
        Falls back to lexical ordering when no dependencies or on cycle.
        """
        # Filter set
        allowed = set(only) if only else set(servers.keys())

        # Build adjacency and indegrees
        adj: dict[str, set[str]] = {name: set() for name in allowed}
        indeg: dict[str, int] = {name: 0 for name in allowed}
        for name in sorted(allowed):
            cfg = servers.get(name) or {}
            deps = cfg.get("dependsOn") or []
            if not isinstance(deps, list):
                continue
            for dep in deps:
                if dep in allowed:
                    adj.setdefault(dep, set()).add(name)
                    indeg[name] = indeg.get(name, 0) + 1

        # Kahn's algorithm
        order: list[str] = []
        queue = [n for n, d in sorted(indeg.items()) if d == 0]
        while queue:
            n = queue.pop(0)
            order.append(n)
            for m in sorted(adj.get(n, set())):
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)

        # If we couldn't include all, fallback to simple lexical
        if len(order) != len(allowed):
            return sorted(allowed)
        return order

    started: dict[str, dict[str, t.Any]] = {}
    skipped: dict[str, str] = {}
    failed: dict[str, str] = {}

    # Deterministic order: explicit filter order if provided, else lexical by name
    names = build_start_order()
    for name in names:
        cfg = servers.get(name)
        if cfg is None:
            failed[name] = "not-found"
            continue
        if not _startable(cfg):
            reason = cfg.get("type", "no-command")
            skipped[name] = f"skip:{reason}"
            continue
        try:
            cmd, cwd = _build_command(cfg)
            env = _resolve_env(cfg.get("env"))
            # Open logs per server
            out_f, err_f = _open_logs(name)
            proc = subprocess.Popen(
                cmd,
                cwd=cwd or None,
                env=env,
                stdout=out_f,
                stderr=err_f,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )
            ok = _wait_readiness(proc, timeout=timeout)
            if ok:
                started[name] = {
                    "pid": proc.pid,
                    "cwd": cwd or str(pathlib.Path.cwd()),
                    "cmd": cmd,
                    "logs": {
                        "out": str((LOG_DIR / f"{name}.out.log").resolve()),
                        "err": str((LOG_DIR / f"{name}.err.log").resolve()),
                    },
                }
            else:
                # Capture last few bytes of stderr to aid debugging
                try:
                    err_path = LOG_DIR / f"{name}.err.log"
                    tail = err_path.read_bytes()[-2048:] if err_path.exists() else b""
                    failed[name] = f"exited-early: {tail.decode(errors='ignore')[-256:]}"
                except Exception:  # noqa: BLE001
                    failed[name] = "exited-early"
        except FileNotFoundError as e:
            failed[name] = f"not-found: {e}"
        except Exception as e:  # noqa: BLE001
            failed[name] = f"error: {e}"

    return {
        "started": started,
        "skipped": skipped,
        "failed": failed,
    }


def _load_mcp_config(path: pathlib.Path) -> dict[str, t.Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"MCP config not found at {path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON in {path}: {e}")
    if not isinstance(data, dict) or "servers" not in data or not isinstance(data["servers"], dict):
        raise SystemExit(".mcp.json must contain a top-level 'servers' object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start all configured MCP servers with local commands")
    parser.add_argument("--only", nargs="*", help="Start only these server names (space-separated)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Seconds to wait for readiness per server")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any start fails")
    args = parser.parse_args(argv)

    cfg = _load_mcp_config(MCP_CONFIG_PATH)
    servers = cfg["servers"]

    res = start_servers(servers, only=args.only, timeout=args.timeout)

    # Summary
    print("\nMCP startup summary:")
    print(f"  started: {len(res['started'])}")
    for name, meta in res["started"].items():
        cmd_str = " ".join(shlex.quote(str(x)) for x in meta["cmd"])[:120]
        print(f"    - {name} (pid={meta['pid']}) :: {cmd_str}")
    print(f"  skipped: {len(res['skipped'])}")
    for name, reason in res["skipped"].items():
        print(f"    - {name} :: {reason}")
    print(f"  failed : {len(res['failed'])}")
    for name, reason in res["failed"].items():
        print(f"    - {name} :: {reason[:160]}")

    if args.strict and res["failed"]:
        return 2
    # Success if at least one started and none failed in strict mode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
