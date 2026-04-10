"""Tests for the OpenRouter provider."""

from types import SimpleNamespace

import pytest

from keep.config import detect_default_providers
from keep.providers.base import EmbedTask
from keep.providers.openrouter import (
    OpenRouterEmbedding,
    OpenRouterSummarization,
    canonicalize_openrouter_model,
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for key in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "KEEP_OPENAI_API_KEY",
        "VOYAGE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "MISTRAL_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "KEEP_LOCAL_ONLY",
    ):
        monkeypatch.delenv(key, raising=False)


class _FakeEmbeddingsAPI:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        input_value = kwargs["input"]
        dimensions = kwargs.get("dimensions", 4)
        if isinstance(input_value, list):
            data = [
                SimpleNamespace(index=1, embedding=[1.0] * dimensions),
                SimpleNamespace(index=0, embedding=[0.0] * dimensions),
            ]
        else:
            data = [SimpleNamespace(index=0, embedding=[0.5] * dimensions)]
        return SimpleNamespace(data=data, model="text-embedding-3-small")


class _FakeChatCompletionsAPI:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )


class _FakeClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddingsAPI()
        self.chat = SimpleNamespace(completions=_FakeChatCompletionsAPI())


class TestOpenRouterModelCanonicalization:

    def test_canonicalizes_bare_openai_embedding_model(self):
        assert canonicalize_openrouter_model("text-embedding-3-small") == "openai/text-embedding-3-small"

    def test_canonicalizes_bare_openai_chat_model(self):
        assert canonicalize_openrouter_model("gpt-4o-mini") == "openai/gpt-4o-mini"

    def test_preserves_known_prefixed_name(self):
        assert canonicalize_openrouter_model("openai/text-embedding-3-small") == "openai/text-embedding-3-small"


class TestOpenRouterEmbedding:

    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            OpenRouterEmbedding(model="openai/text-embedding-3-small")

    def test_embed_passes_dimensions_headers_and_input_type(self, monkeypatch):
        fake_client = _FakeClient()
        captured = {}

        def fake_create_openai_client(*, api_key=None, base_url=None, default_headers=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["default_headers"] = default_headers
            return fake_client

        monkeypatch.setattr("keep.providers.openrouter.create_openai_client", fake_create_openai_client)

        provider = OpenRouterEmbedding(
            model="text-embedding-3-small",
            api_key="sk-or-test",
            dimensions=8,
            site_url="https://keep.local",
            app_name="keep-test",
        )
        result = provider.embed("hello", task=EmbedTask.QUERY)

        assert provider.model_name == "openai/text-embedding-3-small"
        assert len(result) == 8
        assert captured["api_key"] == "sk-or-test"
        assert captured["base_url"] == "https://openrouter.ai/api/v1"
        assert captured["default_headers"] == {
            "HTTP-Referer": "https://keep.local",
            "X-OpenRouter-Title": "keep-test",
        }
        assert fake_client.embeddings.calls[0]["model"] == "openai/text-embedding-3-small"
        assert fake_client.embeddings.calls[0]["dimensions"] == 8
        assert fake_client.embeddings.calls[0]["extra_body"] == {"input_type": "search_query"}

    def test_embed_batch_sorts_by_index(self, monkeypatch):
        fake_client = _FakeClient()
        monkeypatch.setattr(
            "keep.providers.openrouter.create_openai_client",
            lambda **kwargs: fake_client,
        )

        provider = OpenRouterEmbedding(
            model="openai/text-embedding-3-small",
            api_key="sk-or-test",
        )
        result = provider.embed_batch(["a", "b"], task=EmbedTask.DOCUMENT)

        assert result[0] == [0.0] * 4
        assert result[1] == [1.0] * 4
        assert fake_client.embeddings.calls[0]["extra_body"] == {"input_type": "search_document"}


class TestOpenRouterSummarization:

    def test_generate_uses_max_tokens_and_canonicalized_model(self, monkeypatch):
        fake_client = _FakeClient()
        monkeypatch.setattr(
            "keep.providers.openrouter.create_openai_client",
            lambda **kwargs: fake_client,
        )

        provider = OpenRouterSummarization(
            model="gpt-4o-mini",
            api_key="sk-or-test",
        )
        result = provider.generate("system", "user", max_tokens=77)

        assert result == "ok"
        call = fake_client.chat.completions.calls[0]
        assert call["model"] == "openai/gpt-4o-mini"
        assert call["max_tokens"] == 77
        assert "max_completion_tokens" not in call
        assert call["temperature"] == 0.3


class TestOpenRouterConfigDetection:

    def test_detect_default_providers_uses_openrouter_when_direct_keys_absent(self, monkeypatch):
        monkeypatch.setattr("keep.config._detect_ollama", lambda: None)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        providers = detect_default_providers()

        assert providers["embedding"].name == "openrouter"
        assert providers["embedding"].params["model"] == "openai/text-embedding-3-small"
        assert providers["summarization"].name == "openrouter"
        assert providers["summarization"].params["model"] == "openai/gpt-4.1-mini"

    def test_direct_openai_beats_openrouter(self, monkeypatch):
        monkeypatch.setattr("keep.config._detect_ollama", lambda: None)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

        providers = detect_default_providers()

        assert providers["embedding"].name == "openai"
        assert providers["summarization"].name == "openai"

    def test_local_only_excludes_openrouter(self, monkeypatch):
        monkeypatch.setattr("keep.config._detect_ollama", lambda: None)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("KEEP_LOCAL_ONLY", "1")

        providers = detect_default_providers()

        assert providers["embedding"] is None or providers["embedding"].name != "openrouter"
        assert providers["summarization"].name != "openrouter"
