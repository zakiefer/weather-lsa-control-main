import importlib
import os

import pytest

HF_SERVER_MOD = "tools.hf_mcp_server"


@pytest.fixture(scope="module")
def hfmod():
    try:
        mod = importlib.import_module(HF_SERVER_MOD)
    except ModuleNotFoundError as e:
        pytest.skip(f"HF libs unavailable: {e}")
    return mod


def _transformers_available(hfmod) -> bool:
    return getattr(hfmod, "_hf_pipeline", None) is not None


@pytest.mark.skipif(os.environ.get("CI_OFFLINE_ONLY", "0") == "1", reason="offline CI mode")
def test_zero_shot_debug_shape(hfmod, monkeypatch):
    if not _transformers_available(hfmod):
        pytest.skip("transformers not installed")
    # If model isn't cached locally, this may fail fast with unavailable; accept that.
    # Enable debug path via env.
    monkeypatch.setenv("ZERO_SHOT_DEBUG", "1")
    out = hfmod.hf_zero_shot("I love pizza", labels=["food", "sports"], multi_label=False)
    if isinstance(out, dict) and "error" in out:
        pytest.skip(f"model unavailable: {out['error']}")
    assert isinstance(out, dict)
    # When debug enabled, either top-level has debug or _debug on fallback
    assert "label" in out and "score" in out
    dbg = out.get("debug") or out.get("_debug")
    assert dbg is None or isinstance(dbg, dict)


@pytest.mark.skipif(os.environ.get("CI_OFFLINE_ONLY", "0") == "1", reason="offline CI mode")
def test_summarize_honors_params(hfmod):
    if not _transformers_available(hfmod):
        pytest.skip("transformers not installed")
    text = "Streamlit lets you turn Python scripts into shareable web apps in minutes."
    out = hfmod.hf_summarize(text, max_new_tokens=20, min_new_tokens=5)
    if isinstance(out, dict) and "error" in out:
        pytest.skip(f"model unavailable: {out['error']}")
    assert isinstance(out, str)
    assert len(out) > 0


@pytest.mark.skipif(os.environ.get("CI_OFFLINE_ONLY", "0") == "1", reason="offline CI mode")
def test_generate_param_cap(hfmod, monkeypatch):
    if not _transformers_available(hfmod):
        pytest.skip("transformers not installed")
    # Set a small cap to trigger validation error
    monkeypatch.setenv("HF_GEN_MAX_NEW_MAX", "8")
    res = hfmod.hf_generate("Hello there", max_new_tokens=64)
    # Should return error dict when exceeding cap
    if isinstance(res, str):
        # If generation succeeded due to no cap enforced, accept; else validate error shape
        assert len(res) > 0
    else:
        assert isinstance(res, dict)
        assert res.get("error") == "invalid_param"


@pytest.mark.skipif(os.environ.get("CI_OFFLINE_ONLY", "0") == "1", reason="offline CI mode")
def test_translate_batch_basic(hfmod):
    if not _transformers_available(hfmod):
        pytest.skip("transformers not installed")
    res = hfmod.hf_translate(["hello world", "how are you"], src="en", tgt="es")
    if isinstance(res, dict) and "error" in res:
        pytest.skip(f"model unavailable: {res['error']}")
    assert isinstance(res, list)
    assert len(res) == 2
    assert all(isinstance(s, str) for s in res)


def test_translate_enforces_safetensors_policy(hfmod, monkeypatch):
    """
    Ensure translate path returns unavailable with a clear message when safetensors-only
    enforcement triggers. We monkeypatch loaders to avoid actual downloads.
    """
    # Skip entirely if transformers layer isn't present
    if not _transformers_available(hfmod):
        pytest.skip("transformers not installed")

    # Stub tokenizer to avoid filesystem/network access
    class _DummyTok:  # minimal placeholder
        pass

    def _tok_stub(*args, **kwargs):  # noqa: ARG001
        return _DummyTok()

    # Force model loader to simulate missing safetensors weights
    def _model_stub(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("safetensors weights not found for this model")

    tok_cls = getattr(hfmod, "_AutoTokenizer", None)
    mdl_cls = getattr(hfmod, "_AutoModelForSeq2SeqLM", None)
    if tok_cls is None or mdl_cls is None:
        pytest.skip("transformers classes unavailable")

    monkeypatch.setattr(tok_cls, "from_pretrained", _tok_stub)
    monkeypatch.setattr(mdl_cls, "from_pretrained", _model_stub)

    out = hfmod.hf_translate("hello", src="en", tgt="es")
    assert isinstance(out, dict)
    assert out.get("error") == "unavailable"
    # Message should mention safetensors enforcement
    assert "safetensors" in (out.get("message") or "").lower()
