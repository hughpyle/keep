"""Tests for embedding identity configuration."""

from keep.config import EmbeddingIdentity


class TestEmbeddingIdentity:
    """Tests for EmbeddingIdentity dataclass.

    These are pure unit tests - no Keeper, no providers, fast.
    """

    def test_identity_creation(self) -> None:
        """EmbeddingIdentity stores provider, model, and dimension."""
        identity = EmbeddingIdentity(
            provider="sentence-transformers",
            model="all-MiniLM-L6-v2",
            dimension=384,
        )

        assert identity.provider == "sentence-transformers"
        assert identity.model == "all-MiniLM-L6-v2"
        assert identity.dimension == 384

    def test_identity_key_sentence_transformers(self) -> None:
        """Key generation for sentence-transformers."""
        identity = EmbeddingIdentity(
            provider="sentence-transformers",
            model="all-MiniLM-L6-v2",
            dimension=384,
        )

        key = identity.key
        assert "st_" in key
        assert "MiniLM" in key or "minilm" in key.lower()

    def test_identity_key_openai(self) -> None:
        """Key generation for OpenAI."""
        identity = EmbeddingIdentity(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
        )

        key = identity.key
        assert "openai_" in key
