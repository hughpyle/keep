"""Tests for bundled default provider models."""

from types import SimpleNamespace

import pytest

from keep.config import (
    get_default_provider_model,
    merge_default_provider_params,
    load_default_provider_models,
)
from keep.providers.base import get_registry
from keep.providers.mlx import MLXMediaDescriber
from keep.providers.openrouter import OpenRouterEmbedding


def test_bundled_default_provider_models_load():
    defaults = load_default_provider_models()

    assert defaults["embedding"]["voyage"]["model"] == "voyage-3.5-lite"
    assert defaults["summarization"]["openai"]["model"] == "gpt-4.1-mini"
    assert defaults["summarization"]["openrouter"]["model"] == "openai/gpt-4.1-mini"
    assert defaults["media"]["mlx"]["vision_model"] == "mlx-community/Qwen2-VL-2B-Instruct-4bit"
    assert defaults["media"]["mlx"]["whisper_model"] == "mlx-community/whisper-large-v3-turbo"
    assert defaults["content_extractor"]["ollama"]["model"] == "glm-ocr"
    assert defaults["content_extractor"]["mlx"]["model"] == "mlx-community/GLM-OCR-bf16"


def test_runtime_embedding_provider_uses_bundled_default_model(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def fake_create_openai_client(*, api_key=None, base_url=None, default_headers=None):
        return FakeClient()

    monkeypatch.setattr("keep.providers.embeddings.create_openai_client", fake_create_openai_client)

    registry = get_registry()
    provider = registry.create_embedding("openai", {"api_key": "sk-test"})

    assert provider.model_name == get_default_provider_model("embedding", "openai")


def test_runtime_summarization_provider_uses_bundled_default_model(monkeypatch):
    class FakeChatAPI:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeChatAPI())

    monkeypatch.setattr("keep.providers.llm.create_openai_client", lambda **kwargs: FakeClient())

    registry = get_registry()
    provider = registry.create_summarization("openai", {"api_key": "sk-test"})

    assert provider.model_name == get_default_provider_model("summarization", "openai")


def test_explicit_model_overrides_bundled_default(monkeypatch):
    class FakeClient:
        pass

    monkeypatch.setattr("keep.providers.embeddings.create_openai_client", lambda **kwargs: FakeClient())

    registry = get_registry()
    provider = registry.create_embedding(
        "openai",
        {"api_key": "sk-test", "model": "text-embedding-3-large"},
    )

    assert provider.model_name == "text-embedding-3-large"


def test_direct_provider_constructor_requires_explicit_model_context():
    with pytest.raises(ValueError, match="OpenRouterEmbedding requires `model`"):
        OpenRouterEmbedding(api_key="sk-test")


def test_direct_mlx_media_constructor_requires_explicit_model_context():
    with pytest.raises(ValueError, match="MLXMediaDescriber requires `vision_model`"):
        MLXMediaDescriber()


def test_bundled_mlx_media_defaults_populate_constructor_params():
    params = merge_default_provider_params("media", "mlx")

    provider = MLXMediaDescriber(**params)

    assert provider._vision_model == "mlx-community/Qwen2-VL-2B-Instruct-4bit"
    assert provider._whisper_model == "mlx-community/whisper-large-v3-turbo"


def test_bundled_mlx_content_extractor_default_populates_constructor_params():
    params = merge_default_provider_params("content_extractor", "mlx")
    assert params["model"] == "mlx-community/GLM-OCR-bf16"
