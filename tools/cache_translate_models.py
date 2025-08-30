"""
Pre-cache safetensors translation models for offline use.

Behavior:
- Checks local cache for *.safetensors for target translation models.
- For any missing, temporarily enables online mode (HF_HUB_OFFLINE=0) and downloads
    tokenizer + model weights using safetensors only.
- Verifies local cache presence offline afterward.
- Prints a concise summary and writes JSON report under logs/.

Constraints:
- Does NOT upgrade torch or transformers. Uses whatever is installed in the venv.
- Fails fast if upstream repo lacks safetensors files.

Run:
    .venv/bin/python tools/cache_translate_models.py

Optional args:
    --models MODEL_ID [MODEL_ID ...]    # explicit list of model IDs to cache
    --pairs SRC-TGT [SRC-TGT ...]       # language pairs like en-es en-fr (maps to server defaults)

Environment:
    HF_HOME respected if set; otherwise default huggingface cache is used.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _import_or_reexec() -> None:
    """Ensure required libs exist; if not, re-exec with an alternate Python.

    Safety:
    - If already running inside ~/.venvs/mcp-hf/bin/python, don't re-exec (avoid loops).
    - Allow at most 1 re-exec across the process chain; on second attempt, abort.
    - Respect HF_CACHE_PYTHON as an explicit interpreter override.
    """
    # Re-exec safety counter
    reexec_count = 0
    try:
        reexec_count = int(os.environ.get("HF_CACHE_REEXEC_COUNT", "0") or 0)
    except Exception:
        reexec_count = 0
    mcp_py = os.path.expanduser("~/.venvs/mcp-hf/bin/python")
    this_exe = os.path.abspath(sys.executable or sys.argv[0])
    # Hard guard: if re-exec was already attempted once and we still can't import, abort
    if reexec_count >= 1:
        try:
            # Final chance: import here without re-exec
            from huggingface_hub import HfApi  # type: ignore  # noqa: F401
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore  # noqa: F401

            return
        except Exception:
            _eprint("[cache] Re-exec attempted more than once; aborting to avoid loop.")
            _eprint("[cache] Current python:", this_exe)
            _eprint("[cache] Tip: set HF_CACHE_PYTHON to an interpreter with huggingface_hub + transformers installed.")
            sys.exit(2)
    # First try normal presence checks without importing heavy modules here
    try:
        has_hf = importlib.util.find_spec("huggingface_hub") is not None
        has_tf = importlib.util.find_spec("transformers") is not None
        if has_hf and has_tf:
            return
    except Exception:
        pass

    import shutil
    import subprocess

    # If we are already inside the preferred venv or the explicit override, do not re-exec
    env_py = os.environ.get("HF_CACHE_PYTHON")
    try:
        same_as_env = bool(env_py) and os.path.abspath(env_py) == this_exe
    except Exception:
        same_as_env = False
    try:
        same_as_mcp = os.path.abspath(mcp_py) == this_exe
    except Exception:
        same_as_mcp = False
    if same_as_env or same_as_mcp:
        _eprint("[cache] Already running in target interpreter; skipping re-exec.")
        _eprint("[cache] Current python:", this_exe)
        _eprint("[cache] Missing dependencies: install 'huggingface_hub' and 'transformers' here.")
        sys.exit(2)

    candidates: list[str] = []
    # Env override
    if env_py:
        candidates.append(env_py)
    # Preferred MCP HF venv
    candidates.append(os.path.expanduser("~/.venvs/mcp-hf/bin/python"))
    # Last resort: whatever python3 resolves to
    candidates.append(shutil.which("python3") or "python3")

    for exe in candidates:
        if not exe:
            continue
        try:
            # Probe for required imports in the target interpreter
            code = "import huggingface_hub, transformers; print('OK')"
            out = subprocess.check_output([exe, "-c", code], stderr=subprocess.STDOUT, timeout=5)
            if b"OK" in out:
                _eprint(f"[cache] Re-exec using {exe} (has huggingface packages)")
                # Bump re-exec counter and chain-exec
                os.environ["HF_CACHE_REEXEC_COUNT"] = str(reexec_count + 1)
                os.execv(exe, [exe, __file__, *sys.argv[1:]])
        except Exception:
            continue

    _eprint(
        "Missing dependencies: install 'huggingface_hub' and 'transformers' in some interpreter "
        "and set HF_CACHE_PYTHON to its path."
    )
    sys.exit(2)


# Ensure we have the libs in this process or re-exec with another python
_import_or_reexec()

# Now safe to import
from huggingface_hub import HfApi, snapshot_download  # type: ignore
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore

# LocalEntryNotFoundError moved locations across huggingface_hub versions; import compatibly
try:  # huggingface_hub>=0.20 typically
    from huggingface_hub.utils import LocalEntryNotFoundError  # type: ignore
except Exception:  # pragma: no cover - fallback for older/newer variants
    try:
        from huggingface_hub.utils._errors import LocalEntryNotFoundError  # type: ignore
    except Exception:

        class LocalEntryNotFoundError(Exception):  # type: ignore
            pass


# Default targets (pairs -> model ids) — keep in sync with tools/hf_mcp_server.py _PAIR_DEFAULTS
# Prefer safetensors-friendly models by default
DEFAULT_MODELS: list[str] = [
    "Helsinki-NLP/opus-mt-tc-big-en-es",
    "Helsinki-NLP/opus-mt-tc-big-en-fr",
    "Helsinki-NLP/opus-mt-tc-big-fr-en",
    "facebook/wmt19-en-de",
    "facebook/wmt19-de-en",
]


@dataclass
class CacheResult:
    model_id: str
    status: str  # "cached", "already_cached", or "skipped_no_safetensors" or "error"
    detail: str | None = None
    files: list[str] | None = None


def _offline_env() -> tuple[str | None, str | None]:
    return os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE")


def _set_offline(hf_offline: str | None, tr_offline: str | None) -> None:
    if hf_offline is None:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = hf_offline
    if tr_offline is None:
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
    else:
        os.environ["TRANSFORMERS_OFFLINE"] = tr_offline


def _check_local_safetensors(model_id: str) -> list[str]:
    """Return list of local safetensors files for model (offline check)."""
    try:
        # This will raise if snapshot isn't present locally
        repo_path = snapshot_download(
            repo_id=model_id,
            local_files_only=True,
            allow_patterns=["*.safetensors"],
            ignore_patterns=["*.bin"],
        )
    except LocalEntryNotFoundError:
        return []
    except Exception:
        # Any other error — assume not present
        return []
    files: list[str] = []
    for p in Path(repo_path).rglob("*.safetensors"):
        files.append(str(p))
    return files


def _upstream_has_safetensors(api: HfApi, model_id: str) -> bool:
    try:
        files = api.list_repo_files(repo_id=model_id)
        return any(f.endswith(".safetensors") for f in files)
    except Exception:
        return False


def _download_safetensors(model_id: str) -> list[str]:
    # Tokenizer (fast preferred)
    _ = AutoTokenizer.from_pretrained(model_id, use_fast=True)
    # Model weights (safetensors only)
    _ = AutoModelForSeq2SeqLM.from_pretrained(
        model_id,
        use_safetensors=True,
    )
    return _check_local_safetensors(model_id)


def _read_pair_defaults_from_server() -> dict[tuple[str, str], str]:
    """Parse tools/hf_mcp_server.py to extract _PAIR_DEFAULTS without importing it.

    We avoid importing to prevent heavy side-effects and optional dependency requirements.
    """
    server_path = Path(__file__).parent / "hf_mcp_server.py"
    if not server_path.exists():
        return {}
    try:
        src = server_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(server_path))
        for node in tree.body:
            # Handle both simple assignments and annotated assignments (AnnAssign)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_PAIR_DEFAULTS":
                        val = ast.literal_eval(node.value)
                        # Ensure keys are tuples of lowercased str
                        out: dict[tuple[str, str], str] = {}
                        for k, v in val.items():
                            try:
                                s, t = k
                                out[(str(s).lower(), str(t).lower())] = str(v)
                            except Exception:
                                continue
                        return out
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Name) and target.id == "_PAIR_DEFAULTS":
                    val = ast.literal_eval(node.value)  # type: ignore[arg-type]
                    out: dict[tuple[str, str], str] = {}
                    for k, v in val.items():
                        try:
                            s, t = k
                            out[(str(s).lower(), str(t).lower())] = str(v)
                        except Exception:
                            continue
                    return out
    except Exception:
        return {}
    return {}


def _models_from_pairs(pairs: Iterable[str]) -> list[str]:
    mapping = _read_pair_defaults_from_server()
    out: list[str] = []
    for p in pairs:
        p = (p or "").strip().lower()
        if not p:
            continue
        # Accept forms like en-es or en>es or en:es
        for sep in ("-", ">", ":", "/"):
            if sep in p:
                src, tgt = p.split(sep, 1)
                break
        else:
            # If only one token provided, skip
            continue
        pair = (src.strip(), tgt.strip())
        mid = mapping.get(pair)
        if not mid:
            # Fallback heuristic for Helsinki-NLP opus-mt
            mid = f"Helsinki-NLP/opus-mt-{pair[0]}-{pair[1]}"
        out.append(mid)
    # de-dup preserving order
    return list(dict.fromkeys(out))


def cache_models(models: Iterable[str]) -> tuple[list[CacheResult], dict]:
    results: list[CacheResult] = []
    api = HfApi()

    # Save env and set online
    prev_hf, prev_tr = _offline_env()
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"

    try:
        for mid in models:
            # If already cached, record and continue
            existing = _check_local_safetensors(mid)
            if existing:
                results.append(CacheResult(model_id=mid, status="already_cached", files=existing))
                continue
            # Verify upstream supports safetensors
            if not _upstream_has_safetensors(api, mid):
                results.append(
                    CacheResult(
                        model_id=mid,
                        status="skipped_no_safetensors",
                        detail="Upstream repo has no *.safetensors files",
                    )
                )
                continue
            try:
                files = _download_safetensors(mid)
                if files:
                    results.append(CacheResult(model_id=mid, status="cached", files=files))
                else:
                    results.append(CacheResult(model_id=mid, status="error", detail="downloaded but files not found"))
            except Exception as e:  # pragma: no cover - runtime download errors
                results.append(CacheResult(model_id=mid, status="error", detail=str(e)))
    finally:
        # Restore previous offline env
        _set_offline(prev_hf, prev_tr)

    # After caching, ensure we are offline
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    # Verify offline presence for all statuses that should have files
    for r in results:
        if r.status in ("cached", "already_cached"):
            r.files = _check_local_safetensors(r.model_id)
            if not r.files:
                r.status = "error"
                r.detail = "expected files missing after cache"

    # Compose a small summary
    summary = {
        "ts": int(time.time()),
        "offline": True,
        "models": [r.model_id for r in results],
        "counts": {
            "cached": sum(1 for r in results if r.status == "cached"),
            "already_cached": sum(1 for r in results if r.status == "already_cached"),
            "skipped_no_safetensors": sum(1 for r in results if r.status == "skipped_no_safetensors"),
            "error": sum(1 for r in results if r.status == "error"),
        },
    }
    return results, summary


def _print_runtime_info() -> None:
    try:
        import huggingface_hub as _hh  # type: ignore

        hh_ver = getattr(_hh, "__version__", None)
    except Exception:
        hh_ver = None
    venv = os.environ.get("VIRTUAL_ENV") or ""
    print("[cache] Python:", sys.executable)
    if venv:
        print("[cache] VIRTUAL_ENV:", venv)
    print("[cache] huggingface_hub:", hh_ver)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-cache safetensors translation models for offline use")
    parser.add_argument(
        "--models",
        nargs="+",
        help="Explicit model IDs to cache (default: curated Helsinki-NLP opus-mt pairs)",
    )
    parser.add_argument(
        "--pairs",
        nargs="+",
        help="Language pairs like en-es en-fr; will map to default model ids",
    )
    parser.add_argument(
        "--report",
        default="logs/hf_translate_cache_report.json",
        help="Path to write JSON report (default: logs/hf_translate_cache_report.json)",
    )
    args = parser.parse_args(argv)

    # Resolve target models from pairs and/or models
    pair_models: list[str] = _models_from_pairs(args.pairs or []) if args.pairs else []
    base_models: list[str] = list(args.models or [])
    models = list(dict.fromkeys((pair_models + base_models) or DEFAULT_MODELS))

    _print_runtime_info()
    print("[cache] Target models:")
    for m in models:
        print(" -", m)

    results, summary = cache_models(models)

    print()
    print("[cache] Results:")
    for r in results:
        if r.files:
            first = r.files[0] if r.files else ""
            print(f" - {r.model_id}: {r.status} (files={len(r.files)}, e.g., {first})")
        else:
            print(f" - {r.model_id}: {r.status}{(' - ' + r.detail) if r.detail else ''}")

    # Ensure report dir
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "results": [r.__dict__ for r in results],
                "summary": summary,
            },
            f,
            indent=2,
        )
    print(f"\n[cache] Report written: {report_path}")

    # Exit non-zero if any errors or skips due to no safetensors
    if summary["counts"]["error"] > 0:
        return 1
    if summary["counts"]["skipped_no_safetensors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
