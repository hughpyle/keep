"""Regression tests for consistent tag-key validation across write paths."""

from unittest.mock import MagicMock, patch

import pytest

from keep.api import Keeper
from keep.config import ProviderConfig, StoreConfig
from keep.providers.base import Document
from keep.types import MAX_TAG_VALUES_PER_KEY, tag_values, utc_now


def test_invalid_default_tag_key_rejected_on_init(mock_providers, tmp_path):
    """Config default tags must satisfy the same key validation as write tags."""
    config = StoreConfig(
        path=tmp_path,
        config_dir=tmp_path,
        embedding=None,
        summarization=ProviderConfig("truncate"),
        document=ProviderConfig("composite"),
        default_tags={"bad!default": "x"},
    )
    with pytest.raises(ValueError, match="Config default tags"):
        Keeper(store_path=tmp_path, config=config)


def test_invalid_env_tag_key_rejected_on_init(mock_providers, tmp_path, monkeypatch):
    """KEEP_TAG_* keys are validated before any writes occur."""
    monkeypatch.setenv("KEEP_TAG_BAD!ENV", "x")
    with pytest.raises(ValueError, match="environment tags"):
        Keeper(store_path=tmp_path)


def test_invalid_frontmatter_tag_key_rejected(mock_providers, tmp_path):
    """Markdown frontmatter tags must satisfy the shared tag-key rule."""
    class MockMarkdownProvider:
        def fetch(self, uri: str) -> Document:
            return Document(
                uri=uri,
                content="---\nbad!front: yes\n---\nhello\n",
                content_type="text/markdown",
                metadata={},
                tags=None,
            )

    kp = Keeper(store_path=tmp_path)
    kp._document_provider = MockMarkdownProvider()
    try:
        with pytest.raises(ValueError, match="Frontmatter tags"):
            kp.put(uri="file:///mock.md")
    finally:
        kp.close()


def test_move_rejects_invalid_filter_key(mock_providers, tmp_path):
    """move(tags=...) uses the same tag-key validation as list/find filters."""
    kp = Keeper(store_path=tmp_path)
    try:
        with pytest.raises(ValueError, match="invalid characters"):
            kp.move("dest", tags={"bad!key": "x"})
    finally:
        kp.close()


def test_tag_update_rejects_too_many_values_with_source_context(
    mock_providers, tmp_path,
):
    """Tag mutation overflow errors include source context for CLI/API parity."""
    kp = Keeper(store_path=tmp_path)
    try:
        kp.put("x", id="doc:overflow")
        values = [f"v{i}" for i in range(MAX_TAG_VALUES_PER_KEY + 1)]
        with pytest.raises(ValueError, match="Tags: Too many distinct values"):
            kp.tag("doc:overflow", tags={"topic": values})
    finally:
        kp.close()

class TestTagMutations:
    """Value-level tag mutation behavior for Keeper.tag()."""

    def test_tag_remove_single_value(self, mock_providers, tmp_path):
        from keep.api import Keeper
        kp = Keeper(store_path=tmp_path)

        kp.put("meeting notes", id="doc:tag:1", tags={"speaker": ["Alice", "Bob"]})
        result = kp.tag("doc:tag:1", remove_values={"speaker": "Bob"})
        assert result is not None
        assert tag_values(result.tags, "speaker") == ["Alice"]

    def test_tag_remove_and_add_in_one_call(self, mock_providers, tmp_path):
        from keep.api import Keeper
        kp = Keeper(store_path=tmp_path)

        kp.put("meeting notes", id="doc:tag:2", tags={"speaker": ["Alice", "Bob"]})
        result = kp.tag(
            "doc:tag:2",
            tags={"speaker": "Carol"},
            remove_values={"speaker": "Bob"},
        )
        assert result is not None
        assert set(tag_values(result.tags, "speaker")) == {"Alice", "Carol"}

    def test_tag_add_literal_dash_prefix_value(self, mock_providers, tmp_path):
        from keep.api import Keeper
        kp = Keeper(store_path=tmp_path)

        kp.put("meeting notes", id="doc:tag:3", tags={"speaker": "Alice"})
        result = kp.tag("doc:tag:3", tags={"speaker": "-Bob"})
        assert result is not None
        assert set(tag_values(result.tags, "speaker")) == {"Alice", "-Bob"}

    def test_tag_removal_skips_constrained_validation(self, mock_providers, tmp_path):
        from keep.api import Keeper
        from keep.types import utc_now
        kp = Keeper(store_path=tmp_path)
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()

        # Constrained tag setup: only status=open is valid.
        # _requires=act means constraint only applies when act tag is present.
        kp._document_store.upsert(
            doc_coll, ".tag/status", summary="status",
            tags={"_constrained": "true", "_requires": "act",
                  "_created": now, "_updated": now, "_source": "inline"},
        )
        kp._document_store.upsert(
            doc_coll, ".tag/status/open", summary="open",
            tags={"_created": now, "_updated": now, "_source": "inline"},
        )

        # With act tag, constraint is enforced.
        kp.put("item", id="doc:tag:4", tags={"act": "commitment", "status": "open"})

        # Removing a value should not require the removed token to be a valid constrained value.
        result = kp.tag("doc:tag:4", remove_values={"status": "closed"})
        assert result is not None
        assert tag_values(result.tags, "status") == ["open"]

        # Adding an invalid constrained value should still fail (act is on the item).
        with pytest.raises(ValueError, match="Invalid value for constrained tag"):
            kp.tag("doc:tag:4", tags={"status": "closed"})

        # Without act tag, constraint is NOT enforced — any status value is fine.
        kp.put("blog post", id="doc:tag:5", tags={"status": "published"})
        result = kp.get("doc:tag:5")
        assert result is not None
        assert tag_values(result.tags, "status") == ["published"]

    def test_regex_constrained_edge_tag_validates_canonical_target_id(self, mock_providers, tmp_path):
        from keep.api import Keeper
        from keep.types import utc_now

        kp = Keeper(store_path=tmp_path)
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()

        kp._document_store.upsert(
            doc_coll,
            ".tag/frame",
            summary="frame",
            tags={
                "_inverse": "frames",
                "_value_regex": r"^.+\?$",
                "_created": now,
                "_updated": now,
                "_source": "inline",
            },
        )

        kp.put("Investigate restart", id="frame:ok:1", tags={"frame": "debugging?"})
        kp.put(
            "Investigate restart again",
            id="frame:ok:2",
            tags={"frame": "[[repair?|Repair frame]]"},
        )

        with pytest.raises(ValueError, match=r"Value must match regex"):
            kp.put("Bad frame", id="frame:bad:1", tags={"frame": "debugging"})

        with pytest.raises(ValueError, match=r"Value must match regex"):
            kp.put(
                "Bad labeled frame",
                id="frame:bad:2",
                tags={"frame": "[[debugging|Debugging frame]]"},
            )

    def test_regex_constrained_edge_tag_rejects_system_doc_targets(self, mock_providers, tmp_path):
        from keep.api import Keeper
        from keep.types import utc_now

        kp = Keeper(store_path=tmp_path)
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()

        kp._document_store.upsert(
            doc_coll,
            ".tag/frame",
            summary="frame",
            tags={
                "_inverse": "frames",
                "_value_regex": r"^.+\?$",
                "_created": now,
                "_updated": now,
                "_source": "inline",
            },
        )

        with pytest.raises(ValueError, match="system document"):
            kp.put("Bad frame", id="frame:bad:sysdoc", tags={"frame": ".meta/todo?"})

    def test_value_regex_honors_requires_gating(self, mock_providers, tmp_path):
        from keep.api import Keeper
        from keep.types import utc_now

        kp = Keeper(store_path=tmp_path)
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()

        kp._document_store.upsert(
            doc_coll,
            ".tag/channel",
            summary="channel",
            tags={
                "_value_regex": r"^#[a-z]+$",
                "_requires": "act",
                "_created": now,
                "_updated": now,
                "_source": "inline",
            },
        )

        kp.put("No act means no regex gate", id="regex:req:1", tags={"channel": "ops"})
        ok = kp.get("regex:req:1")
        assert ok is not None
        assert tag_values(ok.tags, "channel") == ["ops"]

        kp.put("Act present with valid channel", id="regex:req:2", tags={"act": "commitment", "channel": "#ops"})

        with pytest.raises(ValueError, match=r"Value must match regex"):
            kp.put("Act present with invalid channel", id="regex:req:3", tags={"act": "commitment", "channel": "ops"})

    def test_import_bypasses_value_regex_validation(self, mock_embedding_provider, tmp_path):
        from keep.api import Keeper
        from tests.conftest import MockChromaStore, MockDocumentProvider, MockSummarizationProvider

        mock_reg = MagicMock()
        mock_reg.create_embedding.return_value = mock_embedding_provider
        mock_reg.create_summarization.return_value = MockSummarizationProvider()
        mock_reg.create_document.return_value = MockDocumentProvider()

        with patch("keep.api.get_registry", return_value=mock_reg), \
             patch("keep._provider_lifecycle.get_registry", return_value=mock_reg), \
             patch("keep.api.CachingEmbeddingProvider", side_effect=lambda p, **kw: p), \
             patch("keep._provider_lifecycle.CachingEmbeddingProvider", side_effect=lambda p, **kw: p), \
             patch("keep.store.ChromaStore", MockChromaStore), \
             patch("keep.api.Keeper._spawn_processor", return_value=False):
            kp = Keeper(store_path=tmp_path)

            data = {
                "format": "keep-export",
                "version": 3,
                "documents": [
                    {
                        "id": ".tag/frame",
                        "summary": "frame",
                        "tags": {
                            "_inverse": "frames",
                            "_value_regex": r"^.+\?$",
                            "_created": "2026-01-01T00:00:00",
                            "_updated": "2026-01-01T00:00:00",
                            "_source": "inline",
                        },
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                        "accessed_at": "2026-01-01T00:00:00",
                        "versions": [],
                        "parts": [],
                    },
                    {
                        "id": "import:frame:1",
                        "summary": "Imported frame value bypasses regex validation",
                        "tags": {"frame": "debugging"},
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                        "accessed_at": "2026-01-01T00:00:00",
                        "versions": [],
                        "parts": [],
                    },
                ],
            }

            result = kp.import_data(data)
            assert result["imported"] == 2

            imported = kp.get("import:frame:1")
            assert imported is not None
            assert tag_values(imported.tags, "frame") == ["debugging"]
