"""
Tests for code review fixes: tag queries, SSRF protection, missing embedding provider.
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from keep.document_store import DocumentStore


# ---------------------------------------------------------------------------
# DocumentStore JSON tag queries (json_each / json_extract)
# ---------------------------------------------------------------------------


class TestTagQueries:
    """Tests for list_distinct_tag_keys and list_distinct_tag_values using json_each."""

    @pytest.fixture
    def store(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "documents.db"
            with DocumentStore(db_path) as store:
                yield store

    def test_list_distinct_tag_keys_basic(self, store: DocumentStore) -> None:
        """Returns all user tag keys, sorted."""
        store.upsert("default", "d1", "S1", {"topic": "auth", "project": "web"})
        store.upsert("default", "d2", "S2", {"topic": "db", "status": "open"})

        keys = store.list_distinct_tag_keys("default")
        assert keys == ["project", "status", "topic"]

    def test_list_distinct_tag_keys_excludes_system(self, store: DocumentStore) -> None:
        """System tags (prefixed with _) are excluded."""
        store.upsert("default", "d1", "S1", {
            "topic": "auth",
            "_created": "2026-01-01",
            "_source": "inline",
        })

        keys = store.list_distinct_tag_keys("default")
        assert keys == ["topic"]
        assert "_created" not in keys
        assert "_source" not in keys

    def test_list_distinct_tag_keys_empty_collection(self, store: DocumentStore) -> None:
        """Empty collection returns empty list."""
        keys = store.list_distinct_tag_keys("default")
        assert keys == []

    def test_list_distinct_tag_keys_no_duplicates(self, store: DocumentStore) -> None:
        """Same key across multiple documents appears once."""
        store.upsert("default", "d1", "S1", {"topic": "a"})
        store.upsert("default", "d2", "S2", {"topic": "b"})
        store.upsert("default", "d3", "S3", {"topic": "c"})

        keys = store.list_distinct_tag_keys("default")
        assert keys.count("topic") == 1

    def test_list_distinct_tag_keys_collection_isolation(self, store: DocumentStore) -> None:
        """Keys from other collections are not included."""
        store.upsert("coll1", "d1", "S1", {"alpha": "1"})
        store.upsert("coll2", "d2", "S2", {"beta": "2"})

        assert store.list_distinct_tag_keys("coll1") == ["alpha"]
        assert store.list_distinct_tag_keys("coll2") == ["beta"]

    def test_list_distinct_tag_values_basic(self, store: DocumentStore) -> None:
        """Returns all distinct values for a key, sorted."""
        store.upsert("default", "d1", "S1", {"topic": "auth"})
        store.upsert("default", "d2", "S2", {"topic": "db"})
        store.upsert("default", "d3", "S3", {"topic": "auth"})  # duplicate

        values = store.list_distinct_tag_values("default", "topic")
        assert values == ["auth", "db"]

    def test_list_distinct_tag_values_missing_key(self, store: DocumentStore) -> None:
        """Key not present in any document returns empty list."""
        store.upsert("default", "d1", "S1", {"topic": "auth"})

        values = store.list_distinct_tag_values("default", "nonexistent")
        assert values == []

    def test_list_distinct_tag_values_partial_key(self, store: DocumentStore) -> None:
        """Only documents with the key contribute values."""
        store.upsert("default", "d1", "S1", {"topic": "auth", "status": "open"})
        store.upsert("default", "d2", "S2", {"topic": "db"})  # no status

        values = store.list_distinct_tag_values("default", "status")
        assert values == ["open"]

    def test_query_by_tag_key(self, store: DocumentStore) -> None:
        """query_by_tag_key returns documents having the specified key."""
        store.upsert("default", "d1", "S1", {"topic": "auth"})
        store.upsert("default", "d2", "S2", {"project": "web"})
        store.upsert("default", "d3", "S3", {"topic": "db", "project": "api"})

        results = store.query_by_tag_key("default", "topic")
        ids = {r.id for r in results}
        assert ids == {"d1", "d3"}

    def test_query_by_id_prefix_escapes_wildcards(self, store: DocumentStore) -> None:
        """LIKE wildcards in prefix are escaped, not treated as patterns."""
        store.upsert("default", "normal:1", "S1", {})
        store.upsert("default", "normal:2", "S2", {})
        store.upsert("default", "has%wild", "S3", {})
        store.upsert("default", "has_wild", "S4", {})

        # A prefix of "%" should NOT match everything
        results = store.query_by_id_prefix("default", "%")
        assert len(results) == 0  # no IDs start with literal %

        # A prefix of "_" should NOT match single-char wildcard
        results = store.query_by_id_prefix("default", "_")
        assert len(results) == 0

        # Literal prefix match works
        results = store.query_by_id_prefix("default", "normal:")
        assert len(results) == 2

        # Prefix with literal % matches the doc that has it
        results = store.query_by_id_prefix("default", "has%")
        assert len(results) == 1
        assert results[0].id == "has%wild"


# ---------------------------------------------------------------------------
# HTTP provider SSRF protection (is_private_url)
# ---------------------------------------------------------------------------


class TestIsPrivateUrl:
    """Tests for HttpDocumentProvider._is_private_url SSRF protection."""

    @pytest.fixture
    def provider(self):
        from keep.providers.documents import HttpDocumentProvider
        return HttpDocumentProvider()

    def test_loopback_ipv4(self, provider) -> None:
        assert provider._is_private_url("http://127.0.0.1/secret") is True

    def test_loopback_ipv6(self, provider) -> None:
        assert provider._is_private_url("http://[::1]/secret") is True

    def test_private_10_range(self, provider) -> None:
        assert provider._is_private_url("http://10.0.0.1/internal") is True

    def test_private_172_range(self, provider) -> None:
        assert provider._is_private_url("http://172.16.0.1/internal") is True

    def test_private_192_range(self, provider) -> None:
        assert provider._is_private_url("http://192.168.1.1/internal") is True

    def test_link_local(self, provider) -> None:
        assert provider._is_private_url("http://169.254.169.254/metadata") is True

    def test_cloud_metadata_endpoint(self, provider) -> None:
        assert provider._is_private_url("http://metadata.google.internal/v1") is True

    def test_no_hostname(self, provider) -> None:
        """URLs without a hostname are blocked."""
        assert provider._is_private_url("http:///path") is True

    def test_public_ip(self, provider) -> None:
        assert provider._is_private_url("http://8.8.8.8/dns") is False

    def test_public_domain(self, provider) -> None:
        """Real public domains are allowed."""
        assert provider._is_private_url("https://example.com/page") is False

    def test_localhost_name(self, provider) -> None:
        """localhost resolves to 127.0.0.1, should be blocked."""
        assert provider._is_private_url("http://localhost/secret") is True

    def test_unspecified_address(self, provider) -> None:
        """0.0.0.0 (unspecified) should be blocked."""
        assert provider._is_private_url("http://0.0.0.0/path") is True

    def test_multicast_address(self, provider) -> None:
        """Multicast addresses should be blocked."""
        assert provider._is_private_url("http://224.0.0.1/path") is True

    def test_fetch_blocks_private(self, provider) -> None:
        """fetch() raises IOError for private URLs."""
        with pytest.raises(IOError, match="private"):
            provider.fetch("http://127.0.0.1/secret")

    def test_fetch_blocks_redirect_to_private(self, provider) -> None:
        """fetch() blocks redirects to private addresses."""
        import requests

        mock_resp = MagicMock()
        mock_resp.is_redirect = True
        mock_resp.headers = {"Location": "http://127.0.0.1/internal"}
        mock_resp.close = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(IOError, match="private"):
                provider.fetch("https://example.com/redirect")


# ---------------------------------------------------------------------------
# Embedding provider absent scenarios
# ---------------------------------------------------------------------------


class TestEmbeddingProviderAbsent:
    """Tests for behavior when no embedding provider is configured."""

    def test_get_embedding_provider_raises_with_message(self, tmp_path) -> None:
        """_get_embedding_provider raises RuntimeError with install instructions."""
        from keep.api import Keeper
        from keep.config import StoreConfig

        config = StoreConfig(path=tmp_path, embedding=None)

        with patch("keep.api.load_or_create_config", return_value=config), \
             patch("keep.api.ChromaStore"), \
             patch("keep.document_store.DocumentStore"), \
             patch("keep.api.PendingSummaryQueue"):
            kp = Keeper(store_path=tmp_path)
            with pytest.raises(RuntimeError, match="No embedding provider configured"):
                kp._get_embedding_provider()

    def test_error_message_includes_install_options(self, tmp_path) -> None:
        """Error message mentions pip install and API key options."""
        from keep.api import Keeper
        from keep.config import StoreConfig

        config = StoreConfig(path=tmp_path, embedding=None)

        with patch("keep.api.load_or_create_config", return_value=config), \
             patch("keep.api.ChromaStore"), \
             patch("keep.document_store.DocumentStore"), \
             patch("keep.api.PendingSummaryQueue"):
            kp = Keeper(store_path=tmp_path)
            try:
                kp._get_embedding_provider()
            except RuntimeError as e:
                msg = str(e)
                assert "keep-skill[local]" in msg
                assert "VOYAGE_API_KEY" in msg

    def test_store_config_accepts_none_embedding(self) -> None:
        """StoreConfig can be created with embedding=None."""
        from keep.config import StoreConfig
        config = StoreConfig(path=Path("/tmp/test"), embedding=None)
        assert config.embedding is None
        assert config.summarization.name == "truncate"  # default still works

    def test_save_config_handles_none_embedding(self, tmp_path) -> None:
        """save_config doesn't crash when embedding is None."""
        from keep.config import StoreConfig, save_config

        config = StoreConfig(path=tmp_path, config_dir=tmp_path, embedding=None)
        # Should not raise
        save_config(config)

        # Verify config file exists and doesn't have embedding section
        config_file = tmp_path / "keep.toml"
        assert config_file.exists()
        content = config_file.read_text()
        assert "embedding" not in content or "# embedding" in content
