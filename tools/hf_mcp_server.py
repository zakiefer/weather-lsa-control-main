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
from typing import Any

from huggingface_hub import HfApi, hf_hub_download  # type: ignore[reportMissingImports]
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


"""Sentiment analysis support (lazy-loaded)."""
try:  # pragma: no cover - optional dependency
    from transformers import (  # type: ignore[reportMissingImports]
        AutoModelForSequenceClassification as _AutoModelForSequenceClassification,
        AutoTokenizer as _AutoTokenizer,
        pipeline as _hf_pipeline,
    )
except Exception:  # pragma: no cover - optional dependency
    _hf_pipeline = None  # type: ignore
    _AutoTokenizer = None  # type: ignore
    _AutoModelForSequenceClassification = None  # type: ignore

_SENTIMENT_MODEL_ENV_KEYS = ("SENTIMENT_MODEL_ID", "HF_SENTIMENT_MODEL")
_DEFAULT_SENTIMENT_MODEL = "distilbert-base-uncased-finetuned-sst-2-english"
_sentiment_pipe: Any | None = None


def get_sentiment_pipeline() -> Any:
    """Return a cached transformers sentiment pipeline; load on first use.

    Loads tokenizer and model explicitly for clearer error surfaces and caches the pipeline globally.
    """
    global _sentiment_pipe
    if _sentiment_pipe is not None:
        return _sentiment_pipe
    if _hf_pipeline is None:
        raise RuntimeError(
            "transformers not installed. Install into the HF venv: "
            "~/.venvs/mcp-hf/bin/python -m pip install 'transformers' 'torch'"
        )
    # Pick model id from env overrides
    model_id = None
    for k in _SENTIMENT_MODEL_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            model_id = v
            break
    if not model_id:
        model_id = _DEFAULT_SENTIMENT_MODEL
    try:
        if _AutoTokenizer is not None and _AutoModelForSequenceClassification is not None:
            tokenizer = _AutoTokenizer.from_pretrained(model_id)
            model = _AutoModelForSequenceClassification.from_pretrained(model_id)
            _sentiment_pipe = _hf_pipeline("sentiment-analysis", model=model, tokenizer=tokenizer)
        else:
            # Fallback to simple pipeline construction
            _sentiment_pipe = _hf_pipeline("sentiment-analysis", model=model_id)
        logger.info("Sentiment pipeline ready (model=%s)", model_id)
        return _sentiment_pipe
    except Exception as e:  # pragma: no cover - model download/runtime
        logger.error("Failed to initialize sentiment pipeline: %s", e)
        raise


@server.tool(
    name="hf_sentiment",
    title="Sentiment analysis",
    description=(
        "Run Hugging Face sentiment analysis on text using transformers pipeline. "
        "Set SENTIMENT_MODEL_ID or HF_SENTIMENT_MODEL to override the model "
        "(default: distilbert-base-uncased-finetuned-sst-2-english)."
    ),
)
def hf_sentiment(text: str) -> dict[str, Any]:
    try:
        pipe = get_sentiment_pipeline()
    except Exception as e:  # pragma: no cover - optional env/deps
        logger.warning("hf_sentiment unavailable: %s", e)
        return {"error": "unavailable", "message": str(e)}
    try:
        # transformers may return a list or a single dict depending on version/input
        preds = pipe(text)
        if isinstance(preds, list):
            pred = preds[0] if preds else {"label": "UNKNOWN", "score": 0.0}
        elif isinstance(preds, dict):
            pred = preds
        else:
            pred = {"label": "UNKNOWN", "score": 0.0}
        # normalize keys and return only the required fields
        label = pred.get("label")
        score = float(pred.get("score", 0.0))
        return {"label": label, "score": score}
    except Exception as e:  # pragma: no cover - runtime/model errors
        logger.exception("hf_sentiment runtime error: %s", e)
        return {"text": text, "error": str(e)}


if __name__ == "__main__":
    # Expose Streamable HTTP for detached operation
    # Add a tiny health endpoint
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request):  # type: ignore[reportUnusedFunction]
        return JSONResponse({"ok": True})

    # Best-effort non-blocking warmup of the sentiment pipeline (will download the model on first run)
    def _warmup() -> None:
        try:
            get_sentiment_pipeline()
        except Exception as e:
            logger.info("Sentiment warmup skipped: %s", e)

    try:
        threading.Thread(target=_warmup, name="hf-sentiment-warmup", daemon=True).start()
    except Exception as e:  # pragma: no cover
        logger.debug("Warmup thread not started: %s", e)

    server.run("streamable-http")
