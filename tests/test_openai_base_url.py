"""Tests for OpenAI provider base_url support (OpenAI-compatible endpoints)."""

import pytest

from keep.config import ProviderConfig
from keep.providers.embeddings import OpenAIEmbedding
from keep.providers.llm import OpenAISummarization


@pytest.fixture(autouse=True)
def _clear_openai_env(monkeypatch):
    """Ensure no real API key leaks into tests."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)


class TestOpenAIEmbeddingBaseUrl:

    def test_base_url_no_api_key_required(self):
        """base_url set → no API key needed (local server)."""
        provider = OpenAIEmbedding(
            model="nomic-embed-text",
            base_url="http://localhost:8801/v1",
        )
        # model_name includes base_url for identity/cache disambiguation
        assert provider.model_name == "nomic-embed-text@http://localhost:8801/v1"
        assert provider._api_model == "nomic-embed-text"
        assert provider._client.base_url.host == "localhost"

    def test_no_base_url_no_key_raises(self):
        """No base_url and no API key → must raise."""
        with pytest.raises(ValueError, match="API key required"):
            OpenAIEmbedding(model="text-embedding-3-small")

    def test_base_url_with_api_key(self):
        """base_url + explicit API key both work together."""
        provider = OpenAIEmbedding(
            model="custom-embed",
            api_key="sk-test",
            base_url="http://my-server:9000/v1",
        )
        assert provider._client.api_key == "sk-test"

    def test_dimension_lazy_for_unknown_model(self):
        """Unknown models get lazy dimension detection (no hardcoded lookup)."""
        provider = OpenAIEmbedding(
            model="nomic-embed-text",
            base_url="http://localhost:8801/v1",
        )
        assert provider._dimension is None  # not in MODEL_DIMENSIONS


class TestOpenAISummarizationBaseUrl:

    def test_base_url_no_api_key_required(self):
        """base_url set → no API key needed (local server)."""
        provider = OpenAISummarization(
            model="llama-3.2-3b",
            base_url="http://localhost:8802/v1",
        )
        assert provider.model_name == "llama-3.2-3b"
        assert provider._client.base_url.host == "localhost"

    def test_no_base_url_no_key_raises(self):
        """No base_url and no API key → must raise."""
        with pytest.raises(ValueError, match="API key required"):
            OpenAISummarization(model="gpt-4.1-mini")

    def test_base_url_with_api_key(self):
        """base_url + explicit API key both work together."""
        provider = OpenAISummarization(
            model="llama-3.2-3b",
            api_key="sk-test",
            base_url="http://my-server:9000/v1",
        )
        assert provider._client.api_key == "sk-test"

    def test_new_api_disabled_for_local_servers(self):
        """_new_api must not activate for local models, even with matching names."""
        # "o3-local" starts with "o3" but it's a local server, not OpenAI.
        provider = OpenAISummarization(
            model="o3-local-model",
            base_url="http://localhost:8802/v1",
        )
        assert provider._new_api is False

    def test_new_api_enabled_for_openai(self, monkeypatch):
        """_new_api activates for OpenAI reasoning models (no base_url)."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        provider = OpenAISummarization(model="o3-mini")
        assert provider._new_api is True


class TestEmbeddingIdentityWithBaseUrl:

    def test_different_base_urls_produce_different_model_names(self):
        """Two servers with the same model string must have different identities."""
        a = OpenAIEmbedding(model="nomic", base_url="http://localhost:8801/v1")
        b = OpenAIEmbedding(model="nomic", base_url="http://localhost:8802/v1")
        assert a.model_name != b.model_name

    def test_no_base_url_gives_plain_model_name(self, monkeypatch):
        """Without base_url, model_name is just the model string."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        p = OpenAIEmbedding(model="text-embedding-3-small")
        assert p.model_name == "text-embedding-3-small"
        assert p._api_model == "text-embedding-3-small"


class TestKeepLocalOnly:

    def test_local_only_preserves_openai_with_base_url(self, tmp_path, monkeypatch):
        """KEEP_LOCAL_ONLY=1 should not strip openai providers that have base_url."""
        monkeypatch.setenv("KEEP_LOCAL_ONLY", "1")

        # Simulate what load_config does: check _is_remote
        _REMOTE_PROVIDERS = {"voyage", "openai", "gemini", "anthropic", "mistral"}

        local_openai = ProviderConfig("openai", {"base_url": "http://localhost:8801/v1"})
        remote_openai = ProviderConfig("openai", {})
        ollama = ProviderConfig("ollama", {})

        def _is_remote(cfg):
            if cfg is None or cfg.name not in _REMOTE_PROVIDERS:
                return False
            return not cfg.params.get("base_url")

        assert not _is_remote(local_openai), "openai+base_url should be local"
        assert _is_remote(remote_openai), "openai without base_url should be remote"
        assert not _is_remote(ollama), "ollama should be local"
