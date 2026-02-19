"""Tests for keep data export/import."""

import json
import pytest
from pathlib import Path

from keep.api import Keeper
from keep.config import StoreConfig, ProviderConfig


@pytest.fixture
def keeper(tmp_path):
    """Create a real Keeper with passthrough summarization and no embedding.

    Uses real SQLite (DocumentStore) but no ML models.
    """
    config = StoreConfig(
        path=tmp_path,
        embedding=None,
        summarization=ProviderConfig("passthrough", {"max_chars": 10000}),
        max_summary_length=10000,
    )
    kp = Keeper(str(tmp_path), config=config)
    yield kp
    kp.close()


@pytest.fixture
def fresh_keeper(tmp_path):
    """Create a second Keeper for import testing."""
    fresh_path = tmp_path / "fresh"
    fresh_path.mkdir()
    config = StoreConfig(
        path=fresh_path,
        embedding=None,
        summarization=ProviderConfig("passthrough", {"max_chars": 10000}),
        max_summary_length=10000,
    )
    kp = Keeper(str(fresh_path), config=config)
    yield kp
    kp.close()


def _seed(keeper, docs):
    """Seed documents into keeper via import_batch (bypasses embedding)."""
    ds = keeper._document_store
    coll = keeper._resolve_doc_collection()
    ds.import_batch(coll, docs)


def _make_doc(id, summary, tags=None, versions=None, parts=None,
              created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
              accessed_at="2026-01-01T00:00:00"):
    """Build a document dict for seeding or import."""
    doc = {
        "id": id,
        "summary": summary,
        "tags": tags or {},
        "created_at": created_at,
        "updated_at": updated_at,
        "accessed_at": accessed_at,
    }
    if versions:
        doc["versions"] = versions
    if parts:
        doc["parts"] = parts
    return doc


class TestExportImport:
    """Round-trip export/import tests."""

    def test_export_empty_store(self, keeper):
        """Export from empty store produces valid structure."""
        data = keeper.export_data()
        assert data["format"] == "keep-export"
        assert data["version"] == 1
        assert data["exported_at"]
        assert data["store_info"]["document_count"] >= 0
        assert isinstance(data["documents"], list)

    def test_export_with_documents(self, keeper):
        """Export captures documents with tags and timestamps."""
        _seed(keeper, [
            _make_doc("rust-doc", "Test document about Rust", tags={"topic": "rust"}),
            _make_doc("python-doc", "Another doc about Python", tags={"topic": "python"}),
        ])

        data = keeper.export_data()
        docs_by_id = {d["id"]: d for d in data["documents"]}

        assert "rust-doc" in docs_by_id
        assert "python-doc" in docs_by_id

        doc = docs_by_id["python-doc"]
        assert doc["summary"]
        assert doc["tags"]["topic"] == "python"
        assert doc["created_at"]
        assert doc["updated_at"]
        assert doc["accessed_at"]

    def test_export_with_versions(self, keeper):
        """Export captures version history."""
        _seed(keeper, [
            _make_doc("versioned", "Version 2", versions=[{
                "version": 1,
                "summary": "Version 1",
                "tags": {},
                "content_hash": None,
                "created_at": "2025-12-01T00:00:00",
            }]),
        ])

        data = keeper.export_data()
        doc = next(d for d in data["documents"] if d["id"] == "versioned")

        # Current doc has latest
        assert "Version 2" in doc["summary"]
        # Version history exists
        assert "versions" in doc
        assert len(doc["versions"]) >= 1
        assert data["store_info"]["version_count"] >= 1

    def test_export_exclude_system(self, keeper):
        """--exclude-system skips dot-prefix IDs."""
        _seed(keeper, [
            _make_doc("user-doc", "User doc"),
            _make_doc(".system-doc", "System doc"),
        ])

        data_all = keeper.export_data(include_system=True)
        data_no_sys = keeper.export_data(include_system=False)

        all_ids = {d["id"] for d in data_all["documents"]}
        no_sys_ids = {d["id"] for d in data_no_sys["documents"]}

        assert ".system-doc" in all_ids
        assert "user-doc" in no_sys_ids
        # No dot-prefix IDs in filtered export
        assert all(not id.startswith(".") for id in no_sys_ids)
        # Filtered should have fewer docs
        assert len(data_no_sys["documents"]) < len(data_all["documents"])

    def test_round_trip(self, keeper, fresh_keeper):
        """Export then import into fresh store preserves data."""
        _seed(keeper, [
            _make_doc("auth-learning", "Important learning about auth", tags={"topic": "auth"}),
            _make_doc("notes", "Version 2 of notes", versions=[{
                "version": 1,
                "summary": "Version 1 of notes",
                "tags": {},
                "content_hash": None,
                "created_at": "2025-12-01T00:00:00",
            }]),
        ])

        data = keeper.export_data()
        stats = fresh_keeper.import_data(data, mode="merge")

        assert stats["imported"] > 0
        assert stats["skipped"] == 0

        # Verify imported data
        item = fresh_keeper.get("auth-learning")
        assert item is not None
        assert item.tags.get("topic") == "auth"

        # Verify versions imported
        versions = fresh_keeper.list_versions("notes")
        assert len(versions) >= 1

    def test_merge_skips_existing(self, keeper):
        """Merge mode skips documents with existing IDs."""
        _seed(keeper, [_make_doc("existing-doc", "Original content")])

        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 1, "version_count": 0,
                          "part_count": 0, "collection": "default"},
            "documents": [{
                "id": "existing-doc",
                "summary": "Different content",
                "tags": {},
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "accessed_at": "2026-01-01T00:00:00",
            }],
        }

        stats = keeper.import_data(data, mode="merge")
        assert stats["imported"] == 0
        assert stats["skipped"] == 1

        # Original content preserved
        item = keeper.get("existing-doc")
        assert "Original" in item.summary

    def test_replace_clears_store(self, keeper):
        """Replace mode clears existing data before import."""
        _seed(keeper, [_make_doc("old-doc", "Old data")])

        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 1, "version_count": 0,
                          "part_count": 0, "collection": "default"},
            "documents": [{
                "id": "new-doc",
                "summary": "New data",
                "tags": {"type": "imported"},
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "accessed_at": "2026-01-01T00:00:00",
            }],
        }

        stats = keeper.import_data(data, mode="replace")
        assert stats["imported"] == 1
        assert stats["skipped"] == 0

        # Old doc gone
        assert keeper.get("old-doc") is None
        # New doc present
        item = keeper.get("new-doc")
        assert item is not None
        assert "New data" in item.summary

    def test_import_invalid_format(self, keeper):
        """Import rejects invalid format."""
        with pytest.raises(ValueError, match="Invalid export format"):
            keeper.import_data({"format": "wrong"})

    def test_import_future_version(self, keeper):
        """Import rejects future format versions."""
        with pytest.raises(ValueError, match="not supported"):
            keeper.import_data({"format": "keep-export", "version": 99})

    def test_timestamp_preservation(self, keeper):
        """Import preserves original timestamps."""
        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 1, "version_count": 0,
                          "part_count": 0, "collection": "default"},
            "documents": [{
                "id": "timestamped",
                "summary": "Doc with specific timestamps",
                "tags": {},
                "created_at": "2025-06-15T10:30:00",
                "updated_at": "2025-12-01T14:00:00",
                "accessed_at": "2026-01-15T09:00:00",
            }],
        }

        stats = keeper.import_data(data, mode="merge")
        assert stats["imported"] == 1

        doc_coll = keeper._resolve_doc_collection()
        record = keeper._document_store.get(doc_coll, "timestamped")
        assert record.created_at == "2025-06-15T10:30:00"
        assert record.updated_at == "2025-12-01T14:00:00"
        assert record.accessed_at == "2026-01-15T09:00:00"

    def test_import_queues_reindex(self, keeper):
        """Imported documents are queued for re-embedding."""
        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 2, "version_count": 0,
                          "part_count": 0, "collection": "default"},
            "documents": [
                {
                    "id": "doc1", "summary": "First", "tags": {},
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                },
                {
                    "id": "doc2", "summary": "Second", "tags": {},
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                },
            ],
        }

        stats = keeper.import_data(data, mode="merge")
        assert stats["queued"] == 2


class TestDocumentStoreImport:
    """Direct DocumentStore import method tests."""

    def test_import_batch_basic(self, keeper):
        """import_batch inserts documents correctly."""
        ds = keeper._document_store
        coll = keeper._resolve_doc_collection()

        docs = [{
            "id": "batch-1",
            "summary": "First doc",
            "tags": {"topic": "test"},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-02T00:00:00",
            "accessed_at": "2026-01-03T00:00:00",
            "versions": [{
                "version": 1,
                "summary": "Old version",
                "tags": {},
                "content_hash": None,
                "created_at": "2025-12-01T00:00:00",
            }],
            "parts": [{
                "part_num": 1,
                "summary": "Part one",
                "tags": {"section": "intro"},
                "content": "The introduction text.",
                "created_at": "2026-01-02T00:00:00",
            }],
        }]

        stats = ds.import_batch(coll, docs)
        assert stats == {"documents": 1, "versions": 1, "parts": 1}

        record = ds.get(coll, "batch-1")
        assert record is not None
        assert record.summary == "First doc"
        assert record.tags.get("topic") == "test"
        assert record.created_at == "2026-01-01T00:00:00"
        assert record.updated_at == "2026-01-02T00:00:00"

        versions = ds.list_versions(coll, "batch-1")
        assert len(versions) == 1
        assert versions[0].summary == "Old version"

        parts = ds.list_parts(coll, "batch-1")
        assert len(parts) == 1
        assert parts[0].summary == "Part one"
        assert parts[0].content == "The introduction text."

    def test_delete_collection_all(self, keeper):
        """delete_collection_all clears documents, versions, and parts."""
        ds = keeper._document_store
        coll = keeper._resolve_doc_collection()

        docs = [{
            "id": "to-delete",
            "summary": "Will be deleted",
            "tags": {},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "versions": [{
                "version": 1, "summary": "v1", "tags": {},
                "content_hash": None, "created_at": "2025-01-01T00:00:00",
            }],
            "parts": [{
                "part_num": 1, "summary": "p1", "tags": {},
                "content": "text", "created_at": "2026-01-01T00:00:00",
            }],
        }]
        ds.import_batch(coll, docs)
        assert ds.get(coll, "to-delete") is not None

        count = ds.delete_collection_all(coll)
        assert count >= 1
        assert ds.get(coll, "to-delete") is None
        assert ds.list_versions(coll, "to-delete") == []
        assert ds.list_parts(coll, "to-delete") == []
