"""
Minimal Hugging Face MCP server (stdio) using the Python MCP SDK.

Tools provided (public-only, no token required):
- hf_search_models(query: str, limit: int = 5) -> list of model summaries
- hf_search_datasets(query: str, limit: int = 5) -> list of dataset summaries
- hf_model_card(repo_id: str) -> markdown text of README (if present)
- hf_list_files(repo_id: str, contains: str = "") -> list of repo files (optionally filter by substring)
- hf_download_file(repo_id: str, filename: str, revision: str | None = None) -> local cache path
- hf_whoami() -> auth status summary (works without token, reports unauthenticated)

Run: python tools/hf_mcp_server.py
"""

# ruff: noqa: I001
# pyright: reportMissingImports=false

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from huggingface_hub import HfApi  # type: ignore[reportMissingImports]
from huggingface_hub import hf_hub_download
from huggingface_hub.utils import HfHubHTTPError  # type: ignore[reportMissingImports]
from mcp.server.fastmcp import FastMCP

server = FastMCP(
    name="huggingface",
    instructions="Tools to search and fetch data from the Hugging Face Hub (public repos).",
    host="127.0.0.1",
    port=3865,
    streamable_http_path="/mcp",
    stateless_http=True,
)
logger = logging.getLogger("hf_mcp_server")
logging.basicConfig(level=os.environ.get("HF_MCP_LOGLEVEL", "INFO"))


def _model_summary(m: Any) -> dict[str, Any]:
    # HfApi.list_models returns ModelInfo items
    return {
        "id": getattr(m, "modelId", getattr(m, "id", None)),
        "sha": getattr(m, "sha", None),
        "likes": getattr(m, "likes", None),
        "downloads": getattr(m, "downloads", None),
        "tags": getattr(m, "tags", None),
        "private": getattr(m, "private", None),
        "lastModified": str(getattr(m, "lastModified", "")),
    }


def _dataset_summary(d: Any) -> dict[str, Any]:
    # HfApi.list_datasets returns DatasetInfo items
    return {
        "id": getattr(d, "id", None),
        "sha": getattr(d, "sha", None),
        "likes": getattr(d, "likes", None),
        "downloads": getattr(d, "downloads", None),
        "tags": getattr(d, "tags", None),
        "private": getattr(d, "private", None),
        "lastModified": str(getattr(d, "lastModified", "")),
    }


@server.tool(
    name="hf_search_models",
    title="Search HF models",
    description="Search models on Hugging Face Hub. Returns lightweight summaries.",
)
def hf_search_models(query: str, limit: int = 5) -> list[dict[str, Any]]:
    api = HfApi()
    items = api.list_models(search=query, limit=limit)
    return [_model_summary(m) for m in items]


@server.tool(
    name="hf_search_datasets",
    title="Search HF datasets",
    description="Search datasets on Hugging Face Hub. Returns lightweight summaries.",
)
def hf_search_datasets(query: str, limit: int = 5) -> list[dict[str, Any]]:
    api = HfApi()
    items = api.list_datasets(search=query, limit=limit)
    return [_dataset_summary(d) for d in items]


@server.tool(
    name="hf_model_card",
    title="Get model card (README)",
    description="Fetch README.md for a model repo and return markdown text if available.",
)
def hf_model_card(repo_id: str) -> dict[str, Any]:
    # Try common README filenames
    for fname in ("README.md", "README.MD", "Readme.md"):
        try:
            path = hf_hub_download(repo_id=repo_id, filename=fname)
            with open(path, encoding="utf-8", errors="replace") as f:
                return {"repo_id": repo_id, "filename": fname, "markdown": f.read()}
        except HfHubHTTPError as e:
            # Not found, try next
            last_err = str(e)
        except Exception as e:  # pragma: no cover - defensive
            last_err = str(e)
    return {"repo_id": repo_id, "error": f"README not found or unreadable: {last_err}"}


@server.tool(
    name="hf_list_files",
    title="List repo files",
    description="List files in a repo. Optionally filter by substring.",
)
def hf_list_files(repo_id: str, contains: str = "") -> dict[str, Any]:
    api = HfApi()
    try:
        files = api.list_repo_files(repo_id=repo_id)
        if contains:
            files = [f for f in files if contains in f]
        return {"repo_id": repo_id, "files": files}
    except Exception as e:  # pragma: no cover - network and auth errors
        return {"repo_id": repo_id, "error": str(e)}


@server.tool(
    name="hf_download_file",
    title="Download a file",
    description="Download a single file from a repo into local cache and return the local path.",
)
def hf_download_file(repo_id: str, filename: str, revision: str | None = None) -> dict[str, Any]:
    try:
        path = hf_hub_download(repo_id=repo_id, filename=filename, revision=revision)
        return {"repo_id": repo_id, "filename": filename, "path": path, "size": os.path.getsize(path)}
    except Exception as e:  # pragma: no cover - various errors
        return {"repo_id": repo_id, "filename": filename, "error": str(e)}


@server.tool(
    name="hf_whoami",
    title="Who am I on HF",
    description="Return authentication status. Works without token (will report unauthenticated).",
)
def hf_whoami() -> dict[str, Any]:
    api = HfApi()
    try:
        info = api.whoami()
        return {"authenticated": True, "user": info}
    except Exception as e:  # likely no token
        return {"authenticated": False, "error": str(e)}


"""Sentiment analysis support (lazy-loaded, model-aware, thread-safe)."""
try:  # pragma: no cover - optional dependency
    from transformers import AutoModel as _AutoModel  # feature-extraction fallback
    from transformers import AutoModelForCausalLM as _AutoModelForCausalLM
    from transformers import AutoModelForSeq2SeqLM as _AutoModelForSeq2SeqLM
    from transformers import AutoModelForSequenceClassification as _AutoModelForSequenceClassification  # type: ignore[reportMissingImports]
    from transformers import AutoTokenizer as _AutoTokenizer
    from transformers import pipeline as _hf_pipeline
except Exception:  # pragma: no cover - optional dependency
    _hf_pipeline = None  # type: ignore
    _AutoTokenizer = None  # type: ignore
    _AutoModelForSequenceClassification = None  # type: ignore
    _AutoModel = None  # type: ignore
    _AutoModelForSeq2SeqLM = None  # type: ignore
    _AutoModelForCausalLM = None  # type: ignore

_SENTIMENT_MODEL_ENV_KEYS = ("SENTIMENT_MODEL_ID", "HF_SENTIMENT_MODEL")
_DEFAULT_SENTIMENT_MODEL = "distilbert-base-uncased-finetuned-sst-2-english"
_sentiment_pipes: OrderedDict[str, Any] = OrderedDict()
_sentiment_lock = threading.Lock()
_last_sentiment_error: str | None = None

# Input limits
_MAX_INPUT_CHARS = 2048
_DEFAULT_MAXLEN = int(os.environ.get("HF_SENT_MAXLEN", "256") or 256)
_DEFAULT_TRUNC = os.environ.get("HF_SENT_TRUNC", "true").lower() in ("1", "true", "yes", "y")

# Offline/local cache knobs respected by HF stack
_LOCAL_ONLY = os.environ.get("HF_HUB_OFFLINE", "").lower() in ("1", "true", "yes", "y")
_DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes", "y")


def _resolve_default_model_id() -> str:
    model_id: str | None = None
    for k in _SENTIMENT_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            model_id = v
            break
    return model_id or _DEFAULT_SENTIMENT_MODEL


def get_sentiment_pipeline(model_id: str | None = None) -> Any:
    """Return a cached transformers sentiment pipeline for a model; load on first use.

    Maintains an LRU cache of size 2 keyed by model_id. Thread safe loader.
    """
    global _last_sentiment_error
    if _hf_pipeline is None:
        raise RuntimeError(
            "transformers not installed. Install into the HF venv: "
            "~/.venvs/mcp-hf/bin/python -m pip install 'transformers' 'torch'"
        )
    mid = model_id or _resolve_default_model_id()
    # Fast path without lock
    pipe = _sentiment_pipes.get(mid)
    if pipe is not None:
        return pipe
    # Double-checked lock init
    with _sentiment_lock:
        pipe = _sentiment_pipes.get(mid)
        if pipe is not None:
            return pipe
        try:
            if _AutoTokenizer is not None and _AutoModelForSequenceClassification is not None:
                tokenizer = _AutoTokenizer.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                model = _AutoModelForSequenceClassification.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                pipe = _hf_pipeline("sentiment-analysis", model=model, tokenizer=tokenizer)
            else:
                pipe = _hf_pipeline("sentiment-analysis", model=mid)
            # Insert/update LRU
            _sentiment_pipes[mid] = pipe
            _sentiment_pipes.move_to_end(mid)
            while len(_sentiment_pipes) > 2:
                _sentiment_pipes.popitem(last=False)
            _last_sentiment_error = None
            logger.info("Sentiment pipeline ready (model=%s)", mid)
            return pipe
        except Exception as e:  # pragma: no cover - model download/runtime
            _last_sentiment_error = str(e)
            logger.error("Failed to initialize sentiment pipeline for %s: %s", mid, e)
            raise


@server.tool(
    name="hf_sentiment",
    title="Sentiment analysis",
    description=(
        "Run Hugging Face sentiment analysis on text(s). Accepts a string or list of strings. "
        "Optional model_id selects an alternate model (LRU cached). "
        "Default model is distilbert-base-uncased-finetuned-sst-2-english."
    ),
)
def hf_sentiment(text: Any, model_id: str | None = None) -> Any:
    try:
        pipe = get_sentiment_pipeline(model_id)
    except Exception as e:  # pragma: no cover - optional env/deps
        logger.warning("hf_sentiment unavailable: %s", e)
        return {"error": "unavailable", "message": str(e)}
    try:
        # Prepare inputs: support str or list[str]
        is_single = isinstance(text, str)
        texts: list[str]
        if is_single:
            s = text
            if len(s) > _MAX_INPUT_CHARS:
                s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"  # ellipsis
            texts = [s]
        else:
            texts = []
            for s in list(text):
                s = str(s)
                if len(s) > _MAX_INPUT_CHARS:
                    s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
                texts.append(s)

        preds_any = pipe(texts, truncation=_DEFAULT_TRUNC, max_length=_DEFAULT_MAXLEN)
        # Normalize to list of {label, score}
        norm: list[dict[str, Any]] = []
        if isinstance(preds_any, list):
            # Could be list[dict] or list[list[dict]] depending on return_all_scores
            for item in preds_any:
                if isinstance(item, list) and item:
                    item = item[0]
                if isinstance(item, dict):
                    norm.append({"label": item.get("label"), "score": float(item.get("score", 0.0))})
                else:
                    norm.append({"label": "UNKNOWN", "score": 0.0})
        elif isinstance(preds_any, dict):
            norm.append({"label": preds_any.get("label"), "score": float(preds_any.get("score", 0.0))})
        else:
            norm.append({"label": "UNKNOWN", "score": 0.0})

        return norm[0] if is_single else norm
    except Exception as e:  # pragma: no cover - runtime/model errors
        logger.exception("hf_sentiment runtime error: %s", e)
        return {"error": str(e)}


"""Embeddings (feature extraction) support"""

_EMBED_MODEL_ENV_KEYS = ("HF_EMBED_MODEL",)
_DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_embed_pipes: OrderedDict[str, Any] = OrderedDict()
_embed_lock = threading.Lock()


def _resolve_embed_model_id() -> str:
    for k in _EMBED_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    return _DEFAULT_EMBED_MODEL


def get_embeddings_pipeline(model_id: str | None = None) -> Any:
    if _hf_pipeline is None:
        raise RuntimeError("transformers not installed for embeddings")
    mid = model_id or _resolve_embed_model_id()
    pipe = _embed_pipes.get(mid)
    if pipe is not None:
        return pipe
    with _embed_lock:
        pipe = _embed_pipes.get(mid)
        if pipe is not None:
            return pipe
        try:
            if _AutoTokenizer is not None and _AutoModel is not None:
                tok = _AutoTokenizer.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                mdl = _AutoModel.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                pipe = _hf_pipeline("feature-extraction", model=mdl, tokenizer=tok)
            else:
                pipe = _hf_pipeline("feature-extraction", model=mid)
            _embed_pipes[mid] = pipe
            _embed_pipes.move_to_end(mid)
            while len(_embed_pipes) > 2:
                _embed_pipes.popitem(last=False)
            logger.info("Embeddings pipeline ready (model=%s)", mid)
            return pipe
        except Exception as e:  # pragma: no cover
            logger.error("Failed to init embeddings pipeline for %s: %s", mid, e)
            raise


def _avg_pool(matrix: list[list[float]]) -> list[float]:
    # Average over sequence length without numpy
    if not matrix:
        return []
    # Handle nested shapes like [1][seq_len][hidden] or [layers][seq_len][hidden]
    try:
        if isinstance(matrix[0], list) and matrix[0] and isinstance(matrix[0][0], list):
            # unwrap first dimension
            matrix = matrix[0]  # type: ignore[assignment]
    except Exception:
        pass
    # If it's a single vector [hidden], return as-is
    if matrix and isinstance(matrix[0], (int, float)):
        return [float(x) for x in matrix]  # type: ignore[arg-type]
    seq_len = len(matrix)
    dim = len(matrix[0]) if seq_len else 0
    out = [0.0] * dim
    for row in matrix:
        for i, v in enumerate(row):
            out[i] += float(v)
    if seq_len:
        inv = 1.0 / seq_len
        out = [x * inv for x in out]
    return out


@server.tool(
    name="hf_embeddings",
    title="Text embeddings",
    description=(
        "Generate sentence embeddings. Accepts str or list[str]. "
        "Default model sentence-transformers/all-MiniLM-L6-v2."
    ),
)
def hf_embeddings(text: Any, model_id: str | None = None) -> Any:
    try:
        pipe = get_embeddings_pipeline(model_id)
    except Exception as e:
        return {"error": "unavailable", "message": str(e)}

    is_single = isinstance(text, str)
    texts: list[str] = []
    if is_single:
        s = text
        if len(s) > _MAX_INPUT_CHARS:
            s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
        texts = [s]
    else:
        for s in list(text):
            s = str(s)
            if len(s) > _MAX_INPUT_CHARS:
                s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
            texts.append(s)

    feats_any = pipe(texts, truncation=_DEFAULT_TRUNC, max_length=_DEFAULT_MAXLEN)
    # Normalize: pipeline returns list for batch where each item is [seq_len][hidden]
    if is_single:
        mat = feats_any if isinstance(feats_any, list) else []
        vec = _avg_pool(mat)
        return vec
    out: list[list[float]] = []
    for item in feats_any:
        vec = _avg_pool(item if isinstance(item, list) else [])
        out.append(vec)
    return out


"""Summarization support"""

_SUM_MODEL_ENV_KEYS = ("HF_SUM_MODEL",)
_DEFAULT_SUM_MODEL = "facebook/bart-large-cnn"
_sum_pipes: OrderedDict[str, Any] = OrderedDict()
_sum_lock = threading.Lock()


def _resolve_sum_model_id() -> str:
    for k in _SUM_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    return _DEFAULT_SUM_MODEL


def get_summarize_pipeline(model_id: str | None = None) -> Any:
    if _hf_pipeline is None:
        raise RuntimeError("transformers not installed for summarize")
    mid = model_id or _resolve_sum_model_id()
    pipe = _sum_pipes.get(mid)
    if pipe is not None:
        return pipe
    with _sum_lock:
        pipe = _sum_pipes.get(mid)
        if pipe is not None:
            return pipe
        try:
            if _AutoTokenizer is not None and _AutoModelForSeq2SeqLM is not None:
                tok = _AutoTokenizer.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                mdl = _AutoModelForSeq2SeqLM.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                pipe = _hf_pipeline("summarization", model=mdl, tokenizer=tok)
            else:
                pipe = _hf_pipeline("summarization", model=mid)
            _sum_pipes[mid] = pipe
            _sum_pipes.move_to_end(mid)
            while len(_sum_pipes) > 2:
                _sum_pipes.popitem(last=False)
            logger.info("Summarization pipeline ready (model=%s)", mid)
            return pipe
        except Exception as e:  # pragma: no cover
            logger.error("Failed to init summarization pipeline for %s: %s", mid, e)
            raise


@server.tool(
    name="hf_summarize",
    title="Summarize text",
    description=(
        "Summarize text(s). Accepts str or list[str]. Default model facebook/bart-large-cnn. "
        "Optional model_id, max_new_tokens, min_new_tokens."
    ),
)
def hf_summarize(
    text: Any,
    model_id: str | None = None,
    max_new_tokens: int | None = None,
    min_new_tokens: int | None = None,
) -> Any:
    try:
        pipe = get_summarize_pipeline(model_id)
    except Exception as e:
        return {"error": "unavailable", "message": str(e)}

    is_single = isinstance(text, str)
    texts: list[str] = []
    if is_single:
        s = text
        if len(s) > _MAX_INPUT_CHARS:
            s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
        texts = [s]
    else:
        for s in list(text):
            s = str(s)
            if len(s) > _MAX_INPUT_CHARS:
                s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
            texts.append(s)

    gen_kwargs: dict[str, Any] = {}
    if max_new_tokens is not None:
        gen_kwargs["max_new_tokens"] = int(max_new_tokens)
    if min_new_tokens is not None:
        gen_kwargs["min_new_tokens"] = int(min_new_tokens)

    outs = pipe(
        texts,
        truncation=_DEFAULT_TRUNC,
        max_length=_DEFAULT_MAXLEN,
        **gen_kwargs,
    )

    # Normalize to strings
    def _norm_one(o: Any) -> str:
        if isinstance(o, list) and o:
            o = o[0]
        if isinstance(o, dict):
            return str(o.get("summary_text") or o.get("generated_text") or "")
        return str(o)

    if is_single:
        return _norm_one(outs)
    return [_norm_one(o) for o in outs]


"""Zero-shot classification support"""

_ZS_MODEL_ENV_KEYS = ("HF_ZS_MODEL",)
_DEFAULT_ZS_MODEL = "facebook/bart-large-mnli"
_zs_pipes: OrderedDict[str, Any] = OrderedDict()
_zs_lock = threading.Lock()


def _resolve_zs_model_id() -> str:
    for k in _ZS_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    return _DEFAULT_ZS_MODEL


def get_zero_shot_pipeline(model_id: str | None = None) -> Any:
    if _hf_pipeline is None:
        raise RuntimeError("transformers not installed for zero-shot")
    mid = model_id or _resolve_zs_model_id()
    pipe = _zs_pipes.get(mid)
    if pipe is not None:
        return pipe
    with _zs_lock:
        pipe = _zs_pipes.get(mid)
        if pipe is not None:
            return pipe
        try:
            # Prefer explicit tokenizer/model with local_files_only to honor offline mode
            if _AutoTokenizer is not None and _AutoModelForSequenceClassification is not None:
                tok = _AutoTokenizer.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                mdl = _AutoModelForSequenceClassification.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                pipe = _hf_pipeline("zero-shot-classification", model=mdl, tokenizer=tok)
            else:
                # Fallback: let pipeline resolve model by id
                pipe = _hf_pipeline("zero-shot-classification", model=mid)
            _zs_pipes[mid] = pipe
            _zs_pipes.move_to_end(mid)
            while len(_zs_pipes) > 2:
                _zs_pipes.popitem(last=False)
            logger.info("Zero-shot pipeline ready (model=%s)", mid)
            return pipe
        except Exception as e:  # pragma: no cover
            logger.error("Failed to init zero-shot pipeline for %s: %s", mid, e)
            raise


@server.tool(
    name="hf_zero_shot",
    title="Zero-shot classification",
    description=(
        "Zero-shot classification on text(s) with provided labels. Accepts str or list[str]. "
        "Default model facebook/bart-large-mnli. Optional multi_label boolean."
    ),
)
def hf_zero_shot(
    text: Any,
    labels: list[str],
    model_id: str | None = None,
    multi_label: bool | None = False,
    debug: bool | None = None,
) -> Any:
    try:
        pipe = get_zero_shot_pipeline(model_id)
    except Exception as e:
        return {"error": "unavailable", "message": str(e)}

    is_single = isinstance(text, str)
    texts: list[str] = []
    if is_single:
        s = text
        if len(s) > _MAX_INPUT_CHARS:
            s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
        texts = [s]
    else:
        for s in list(text):
            s = str(s)
            if len(s) > _MAX_INPUT_CHARS:
                s = s[: _MAX_INPUT_CHARS - 1] + "\u2026"
            texts.append(s)

    outs = pipe(
        texts,
        candidate_labels=list(labels),
        multi_label=bool(multi_label),
        truncation=_DEFAULT_TRUNC,
        max_length=_DEFAULT_MAXLEN,
    )

    # Lightweight debug about output structure (no input text logged)
    try:
        if isinstance(outs, dict):
            logger.debug(
                "zero-shot outs (dict): keys=%s labels_len=%s scores_len=%s",
                list(outs.keys()),
                len(outs.get("labels", []) if isinstance(outs.get("labels"), list) else []),
                len(outs.get("scores", []) if isinstance(outs.get("scores"), list) else []),
            )
        elif isinstance(outs, list):
            first = outs[0] if outs else None
            if isinstance(first, dict):
                logger.debug(
                    "zero-shot outs (list): n=%d first_keys=%s first_labels_len=%s first_scores_len=%s",
                    len(outs),
                    list(first.keys()),
                    len(first.get("labels", []) if isinstance(first.get("labels"), list) else []),
                    len(first.get("scores", []) if isinstance(first.get("scores"), list) else []),
                )
            else:
                logger.debug("zero-shot outs (list): n=%d first_type=%s", len(outs), type(first).__name__)
        else:
            logger.debug("zero-shot outs (type=%s)", type(outs).__name__)
    except Exception:
        pass

    # Optional debug block toggle via env ZERO_SHOT_DEBUG or explicit debug arg in future
    _zs_dbg_env = bool(debug) or (os.environ.get("ZERO_SHOT_DEBUG", "").lower() in ("1", "true", "yes"))

    def _top_or_all(o: Any) -> Any:
        if isinstance(o, dict):
            labs = o.get("labels") or []
            scs = o.get("scores") or []
            pairs = [{"label": str(lbl), "score": float(scr)} for lbl, scr in zip(labs, scs)]
            if multi_label:
                # When multi_label, return all pairs; add debug if toggled
                if _zs_dbg_env:
                    return {
                        "labels": [str(x) for x in labs],
                        "scores": [float(x) for x in scs],
                        "model_id": (model_id or _resolve_zs_model_id()),
                        "params": {"multi_label": bool(multi_label)},
                        "pairs": pairs,
                    }
                return pairs
            if pairs:
                top = pairs[0]
                if _zs_dbg_env:
                    return {
                        "label": top["label"],
                        "score": top["score"],
                        "debug": {
                            "labels": [str(x) for x in labs],
                            "scores": [float(x) for x in scs],
                            "model_id": (model_id or _resolve_zs_model_id()),
                            "params": {"multi_label": bool(multi_label)},
                        },
                    }
                return top
            # Debug: expose structure when unexpected empty result
            return {
                "label": "UNKNOWN",
                "score": 0.0,
                "_debug": {
                    "labels_len": len(labs) if hasattr(labs, "__len__") else None,
                    "scores_len": len(scs) if hasattr(scs, "__len__") else None,
                    "keys": list(o.keys()),
                },
            }
        return {"label": "UNKNOWN", "score": 0.0, "_debug": {"type": type(o).__name__}}

    if is_single:
        # For single-text input, the pipeline returns a dict OR a list with one dict.
        if isinstance(outs, list) and outs:
            return _top_or_all(outs[0])
        return _top_or_all(outs)
    # Batch path: ensure we iterate over list of outputs
    if isinstance(outs, list):
        return [_top_or_all(o) for o in outs]
    # Defensive: if a single dict was returned unexpectedly, wrap it
    return [_top_or_all(outs)]


"""Text generation support"""

_GEN_MODEL_ENV_KEYS = ("HF_GEN_MODEL",)
_DEFAULT_GEN_MODEL = "distilgpt2"
_gen_pipes: OrderedDict[str, Any] = OrderedDict()
_gen_lock = threading.Lock()


def _resolve_gen_model_id() -> str:
    for k in _GEN_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    return _DEFAULT_GEN_MODEL


def get_generate_pipeline(model_id: str | None = None) -> Any:
    if _hf_pipeline is None:
        raise RuntimeError("transformers not installed for generate")
    mid = model_id or _resolve_gen_model_id()
    pipe = _gen_pipes.get(mid)
    if pipe is not None:
        return pipe
    with _gen_lock:
        pipe = _gen_pipes.get(mid)
        if pipe is not None:
            return pipe
        try:
            if _AutoTokenizer is not None and _AutoModelForCausalLM is not None:
                tok = _AutoTokenizer.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                mdl = _AutoModelForCausalLM.from_pretrained(mid, local_files_only=_LOCAL_ONLY)
                pipe = _hf_pipeline("text-generation", model=mdl, tokenizer=tok)
            else:
                pipe = _hf_pipeline("text-generation", model=mid)
            _gen_pipes[mid] = pipe
            _gen_pipes.move_to_end(mid)
            while len(_gen_pipes) > 2:
                _gen_pipes.popitem(last=False)
            logger.info("Generation pipeline ready (model=%s)", mid)
            return pipe
        except Exception as e:  # pragma: no cover
            logger.error("Failed to init generation pipeline for %s: %s", mid, e)
            raise


@server.tool(
    name="hf_generate",
    title="Text generation",
    description=(
        "Generate continuation(s) for a prompt. Accepts str or list[str]. Default model distilgpt2. "
        "Optional model_id, max_new_tokens (<= cap), temperature, top_p, do_sample."
    ),
)
def hf_generate(
    text: Any,
    model_id: str | None = None,
    max_new_tokens: int | None = 64,
    temperature: float | None = 0.7,
    top_p: float | None = 0.9,
    do_sample: bool | None = True,
) -> Any:
    try:
        pipe = get_generate_pipeline(model_id)
    except Exception as e:
        return {"error": "unavailable", "message": str(e)}

    # Hard caps
    cap = int(os.environ.get("HF_GEN_MAX_NEW_MAX", "256") or 256)
    requested = int(max_new_tokens) if max_new_tokens is not None else 64
    if requested < 1 or requested > cap:
        return {
            "error": "invalid_param",
            "message": f"max_new_tokens {requested} outside allowed range [1,{cap}]",
        }

    max_prompt = int(os.environ.get("HF_GEN_MAXLEN", "256") or 256)
    is_single = isinstance(text, str)
    texts: list[str] = []
    if is_single:
        s = text
        if len(s) > max_prompt:
            s = s[: max_prompt - 1] + "\u2026"
        texts = [s]
    else:
        for s in list(text):
            s = str(s)
            if len(s) > max_prompt:
                s = s[: max_prompt - 1] + "\u2026"
            texts.append(s)

    gen_kwargs: dict[str, Any] = {
        "max_new_tokens": requested,
        "temperature": float(temperature) if temperature is not None else 0.7,
        "top_p": float(top_p) if top_p is not None else 0.9,
        "do_sample": bool(do_sample) if do_sample is not None else True,
        "truncation": True,
        "max_length": max_prompt,
    }

    try:
        outs = pipe(texts, **gen_kwargs)
    except Exception as e:  # pragma: no cover
        logger.exception("hf_generate runtime error: %s", e)
        return {"error": str(e)}

    def _norm_one(o: Any) -> str:
        if isinstance(o, list) and o:
            o = o[0]
        txt = ""
        if isinstance(o, dict):
            txt = str(o.get("generated_text") or "")
        else:
            txt = str(o)
        return txt.strip()

    if is_single:
        return _norm_one(outs)
    return [_norm_one(o) for o in outs]


"""Translation support"""

_TX_MODEL_ENV_KEYS = ("HF_TX_MODEL",)
# Prefer safetensors-friendly defaults
_DEFAULT_TX_MODEL = "Helsinki-NLP/opus-mt-tc-big-en-es"
_tx_pipes: OrderedDict[str, Any] = OrderedDict()
_tx_lock = threading.Lock()

_PAIR_DEFAULTS: dict[tuple[str, str], str] = {
    # Use models that publish safetensors weights
    ("en", "es"): "Helsinki-NLP/opus-mt-tc-big-en-es",
    ("en", "fr"): "Helsinki-NLP/opus-mt-tc-big-en-fr",
    ("fr", "en"): "Helsinki-NLP/opus-mt-tc-big-fr-en",
    ("en", "de"): "facebook/wmt19-en-de",
    ("de", "en"): "facebook/wmt19-de-en",
}


def _resolve_tx_model_id(src: str | None, tgt: str | None, model_id: str | None) -> tuple[str, str, str]:
    s = (src or "en").lower()
    t = (tgt or "es").lower()
    if model_id:
        return s, t, model_id
    pair = (s, t)
    mid = _PAIR_DEFAULTS.get(pair)
    if not mid:
        # Fall back to env override when no mapping
        env_mid = os.environ.get("HF_TX_MODEL")
        if env_mid:
            mid = env_mid
        else:
            mid = _DEFAULT_TX_MODEL
    return s, t, mid


def get_translate_pipeline(model_id: str) -> Any:
    if _hf_pipeline is None:
        raise RuntimeError("transformers not installed for translate")
    pipe = _tx_pipes.get(model_id)
    if pipe is not None:
        return pipe
    with _tx_lock:
        pipe = _tx_pipes.get(model_id)
        if pipe is not None:
            return pipe
        try:
            # Security: enforce local-only and safetensors-only for translate to avoid torch.load of .bin
            if _AutoTokenizer is None or _AutoModelForSeq2SeqLM is None:
                raise RuntimeError(
                    "translate requires transformers AutoTokenizer and AutoModelForSeq2SeqLM; safetensors-only enforced"
                )
            tok = _AutoTokenizer.from_pretrained(model_id, local_files_only=True)
            # use_safetensors=True ensures we never attempt to load legacy .bin weights via torch.load
            # Avoid FP16 on CPU (LayerNorm not implemented); prefer float32 if torch is available
            try:  # lightweight import inside critical path
                import torch as _torch  # type: ignore

                _dtype: object = _torch.float32
            except Exception:  # pragma: no cover - if torch can't be imported, fall back to auto
                _dtype = "auto"

            mdl = _AutoModelForSeq2SeqLM.from_pretrained(
                model_id,
                local_files_only=True,
                use_safetensors=True,
                torch_dtype=_dtype,
            )
            pipe = _hf_pipeline("translation", model=mdl, tokenizer=tok)
            _tx_pipes[model_id] = pipe
            _tx_pipes.move_to_end(model_id)
            while len(_tx_pipes) > 2:
                _tx_pipes.popitem(last=False)
            logger.info("Translation pipeline ready (model=%s)", model_id)
            return pipe
        except Exception as e:  # pragma: no cover
            logger.error("Failed to init translation pipeline for %s: %s", model_id, e)
            raise


@server.tool(
    name="hf_translate",
    title="Translate text",
    description=(
        "Translate text(s). Defaults en→es; accepts src/tgt language codes and optional model_id. "
        "Optional: max_length (int), num_beams (int), min_new_tokens (int). "
        "Returns str or list[str] matching input shape."
    ),
)
def hf_translate(
    text: Any,
    src: str | None = None,
    tgt: str | None = None,
    model_id: str | None = None,
    max_length: int | None = None,
    num_beams: int | None = None,
    min_new_tokens: int | None = None,
) -> Any:
    s, t, mid = _resolve_tx_model_id(src, tgt, model_id)
    try:
        pipe = get_translate_pipeline(mid)
    except Exception as e:
        # Normalize message to make probe/test reasons clear
        msg = str(e)
        if "use_safetensors" in msg or "safetensors" in msg:
            msg = f"translate unavailable (safetensors-only enforced): {msg}"
        elif "local_files_only" in msg or "local-only" in msg:
            msg = f"translate unavailable (local-only cache required): {msg}"
        return {"error": "unavailable", "message": msg}

    max_chars = 2048
    # Default caps; allow env override but keep conservative upper bound
    max_len = int(os.environ.get("HF_TX_MAXLEN", "256") or 256)
    if max_length is not None:
        try:
            # Never exceed env default to remain resource-friendly
            max_len = max(8, min(max_len, int(max_length)))
        except Exception:  # pragma: no cover - defensive
            pass
    # Beam search control (defaults to 1 = greedy for speed)
    beams = int(os.environ.get("HF_TX_BEAMS", "1") or 1)
    if num_beams is not None:
        try:
            beams = max(1, min(4, int(num_beams)))
        except Exception:  # pragma: no cover
            pass

    # Encourage non-empty generations: enforce a small minimum by default
    # Env HF_TX_MIN_NEW overrides; explicit arg wins.
    try:
        default_min_new = int(os.environ.get("HF_TX_MIN_NEW", "1") or 1)
    except Exception:  # pragma: no cover
        default_min_new = 1
    if min_new_tokens is not None:
        try:
            # keep within a tiny safe range
            min_new = max(1, min(8, int(min_new_tokens)))
        except Exception:  # pragma: no cover
            min_new = max(1, min(8, default_min_new))
    else:
        min_new = max(1, min(8, default_min_new))
    is_single = isinstance(text, str)
    texts: list[str] = []
    if is_single:
        stext = text
        if len(stext) > max_chars:
            stext = stext[: max_chars - 1] + "\u2026"
        texts = [stext]
    else:
        for s_ in list(text):
            s_ = str(s_)
            if len(s_) > max_chars:
                s_ = s_[: max_chars - 1] + "\u2026"
            texts.append(s_)

    try:
        outs = pipe(
            texts,
            truncation=True,
            max_length=max_len,
            num_beams=beams,
            min_new_tokens=min_new,
        )
    except Exception as e:  # pragma: no cover
        logger.exception("hf_translate runtime error: %s", e)
        return {"error": str(e)}

    def _norm_one(o: Any) -> str:
        if isinstance(o, list) and o:
            o = o[0]
        if isinstance(o, dict):
            return str(o.get("translation_text") or o.get("generated_text") or "").strip()
        return str(o).strip()

    if is_single:
        return _norm_one(outs)
    return [_norm_one(o) for o in outs]


if __name__ == "__main__":
    # Expose Streamable HTTP for detached operation
    # Add a tiny health endpoint
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request):  # type: ignore[reportUnusedFunction]
        return JSONResponse({"ok": True})

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request):  # type: ignore[reportUnusedFunction]
        # Summarize current health and model status without forcing a load
        try:
            import transformers as _t  # type: ignore
        except Exception:  # pragma: no cover
            _t = None
        try:
            import torch as _tc  # type: ignore
        except Exception:  # pragma: no cover
            _tc = None
        try:
            import sentencepiece as _sp  # type: ignore

            _spv = getattr(_sp, "__version__", None)
        except Exception:  # pragma: no cover
            _sp = None
            _spv = None
        try:
            import sacremoses as _sm  # type: ignore

            _smv = getattr(_sm, "__version__", None)
        except Exception:  # pragma: no cover
            _sm = None
            _smv = None
        import sys as _sys  # safe

        default_model = _resolve_default_model_id()
        loaded = default_model in _sentiment_pipes

        # Helper to format cache info without triggering loads
        def _cache_info(d: OrderedDict[str, Any]) -> dict[str, Any]:
            return {"size": len(d), "models": list(d.keys())}

        body: dict[str, Any] = {
            "ok": True,
            "model_loaded": loaded,
            "model_id": default_model,
            "transformers": getattr(_t, "__version__", None),
            "torch": getattr(_tc, "__version__", None),
            "python": getattr(_sys, "executable", None),
            "offline": {
                "HF_HUB_OFFLINE": bool(os.environ.get("HF_HUB_OFFLINE")),
                "TRANSFORMERS_OFFLINE": bool(os.environ.get("TRANSFORMERS_OFFLINE")),
                "HF_DATASETS_OFFLINE": bool(os.environ.get("HF_DATASETS_OFFLINE")),
                "HF_HUB_ENABLE_HF_TRANSFER": bool(os.environ.get("HF_HUB_ENABLE_HF_TRANSFER")),
                "HF_HOME": os.environ.get("HF_HOME"),
            },
            "deps": {
                "sentencepiece": _spv,
                "sacremoses": _smv,
            },
            "caches": {
                "sentiment": _cache_info(_sentiment_pipes),
                "embeddings": _cache_info(globals().get("_embed_pipes", OrderedDict())),
                "summarize": _cache_info(globals().get("_sum_pipes", OrderedDict())),
                "zero_shot": _cache_info(globals().get("_zs_pipes", OrderedDict())),
                "generate": _cache_info(globals().get("_gen_pipes", OrderedDict())),
                "translate": _cache_info(globals().get("_tx_pipes", OrderedDict())),
            },
            "policies": {
                "translate": {
                    "local_files_only": True,
                    "safetensors_only": True,
                }
            },
        }
        if _last_sentiment_error:
            body["last_error"] = _last_sentiment_error
        return JSONResponse(body)

    # Best-effort non-blocking warmup of pipelines (strictly opt-in; default is no warmup to avoid any IO)
    def _warmup() -> None:
        try:
            if os.environ.get("HF_SENT_WARMUP", "0").lower() in ("1", "true", "yes", "y"):
                try:
                    get_sentiment_pipeline()
                except Exception as se:
                    logger.info("Sentiment warmup skipped: %s", se)

            # Optional: background warmup for default translation model so first call is fast
            if os.environ.get("HF_TX_WARMUP", "0").lower() in ("1", "true", "yes", "y"):
                try:
                    get_translate_pipeline(_DEFAULT_TX_MODEL)
                except Exception as te:  # pragma: no cover - optional
                    logger.info("Translate warmup skipped: %s", te)
        except Exception as e:  # pragma: no cover
            logger.debug("Warmup checks failed: %s", e)

    try:
        threading.Thread(target=_warmup, name="hf-sentiment-warmup", daemon=True).start()
    except Exception as e:  # pragma: no cover
        logger.debug("Warmup thread not started: %s", e)

    server.run("streamable-http")
