"""Tests for keep data export/import."""

import json
import pytest
import yaml
from pathlib import Path

from keep.api import Keeper
from keep.cli_app import (
    _MAX_FILENAME_BYTES,
    _id_to_rel_path,
    _md_link_target,
    _render_doc_markdown,
    _render_part_markdown,
    _render_version_markdown,
    _write_markdown_export,
)
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
        assert data["version"] == 2
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

    def test_export_include_system_flag(self, keeper):
        """include_system=False (the CLI default) skips dot-prefix IDs."""
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

    def test_import_rejects_invalid_tag_key(self, keeper):
        """Import rejects tag keys that violate tag-key validation."""
        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 1, "version_count": 0,
                          "part_count": 0, "collection": "default"},
            "documents": [{
                "id": "bad-tags",
                "summary": "Doc with invalid tag key",
                "tags": {"bad!key": "x"},
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "accessed_at": "2026-01-01T00:00:00",
            }],
        }

        with pytest.raises(ValueError, match="Import document tags"):
            keeper.import_data(data, mode="merge")

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

    def test_import_v1_part_content_normalizes_into_summary(self, keeper):
        """Legacy v1 part content is promoted into the canonical part summary."""
        data = {
            "format": "keep-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00",
            "store_info": {"document_count": 1, "version_count": 0,
                          "part_count": 1, "collection": "default"},
            "documents": [{
                "id": "legacy-part-doc",
                "summary": "Parent summary",
                "tags": {},
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "accessed_at": "2026-01-01T00:00:00",
                "parts": [{
                    "part_num": 1,
                    "summary": "Old short prefix",
                    "content": "Full legacy part text",
                    "tags": {"topic": "legacy"},
                    "created_at": "2026-01-01T00:00:00",
                }],
            }],
        }

        stats = keeper.import_data(data, mode="merge")
        assert stats["queued"] == 2

        part = keeper._document_store.get_part(
            keeper._resolve_doc_collection(),
            "legacy-part-doc",
            1,
        )
        assert part is not None
        assert part.summary == "Full legacy part text"

    def test_export_parts_omit_legacy_content_field(self, keeper):
        """Version 2 exports write summary-only part records."""
        _seed(keeper, [_make_doc(
            "summary-only",
            "Parent summary",
            parts=[{
                "part_num": 1,
                "summary": "Canonical part text",
                "tags": {"topic": "x"},
                "created_at": "2026-01-02T00:00:00",
            }],
        )])

        data = keeper.export_data()
        doc = next(d for d in data["documents"] if d["id"] == "summary-only")
        assert data["version"] == 2
        assert doc["parts"] == [{
            "part_num": 1,
            "summary": "Canonical part text",
            "tags": {"topic": "x"},
            "created_at": "2026-01-02T00:00:00",
        }]


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
                "created_at": "2026-01-01T00:00:00",
            }],
        }]
        ds.import_batch(coll, docs)
        assert ds.get(coll, "to-delete") is not None

        count = ds.delete_collection_all(coll)
        assert count >= 1
        assert ds.get(coll, "to-delete") is None
        assert ds.list_versions(coll, "to-delete") == []
        assert ds.list_parts(coll, "to-delete") == []


def _parse_markdown(path: Path) -> tuple[dict, str]:
    """Parse an exported markdown file into (frontmatter_dict, body)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter: {text[:40]!r}"
    _, fm_text, body = text.split("---", 2)
    return yaml.safe_load(fm_text), body.lstrip("\n")


class TestMarkdownExport:
    """Tests for the markdown export mode (directory, one .md per note)."""

    def test_id_to_rel_path_simple(self):
        assert _id_to_rel_path("auth-notes") == Path("auth-notes.md")

    def test_id_to_rel_path_file_uri_becomes_dir_tree(self):
        # file:///Users/x/README.md mirrors the directory structure
        # with the URI scheme as a top-level directory (wget -m style).
        rel = _id_to_rel_path("file:///Users/x/README.md")
        assert rel == Path("file") / "Users" / "x" / "README.md.md"

    def test_id_to_rel_path_system_ids_nest(self):
        # Hierarchical ids split on `/` and become nested directories.
        assert _id_to_rel_path(".tag/act/commitment") == Path(".tag/act/commitment.md")

    def test_id_to_rel_path_hierarchical_without_scheme(self):
        assert _id_to_rel_path("notes/2024/jan") == Path("notes/2024/jan.md")

    def test_id_to_rel_path_encodes_unsafe_chars_within_component(self):
        # ':' and '#' inside a single path component are percent-encoded.
        rel = _id_to_rel_path("file:///a/b:c#d/e")
        assert rel == Path("file") / "a" / "b%3Ac%23d" / "e.md"

    def test_id_to_rel_path_truncates_overlong_thread_id(self):
        # Real-world case: long thread ids with embedded base64 blobs
        # exceed the filesystem's per-component byte limit.  With the
        # bare-scheme rule, `thread:` peels off as a directory and the
        # long body is one component that gets truncated + hashed.
        long_id = (
            "thread:3d0b982edfb41fea27a4e3f4b86fe0598c4947f0@hey.com"
            "#" + "BAh7CEkiCGdpZAY6BkVUSSI9Z2lkOi8vaGF5c3RhY2svQWN0aXZlU3RvcmFnZTo6"
            "QmxvYi8xMDc3MTA1MzExP2V4cGlyZXNfaW4GOwBUSSIMcHVycG9zZQY7A"
            "FRJIg9hdHRhY2hhYmxlBjsAVEkiD2V4cGlyZXNfYXQGOwBUMA%3D%3D"
            "--5d3054b37eb866d54f494788389df9126e377521@hey.com"
        )
        rel = _id_to_rel_path(long_id)
        # Two parts: 'thread' dir + the (truncated) body file.
        assert rel.parts[0] == "thread"
        assert len(rel.parts) == 2
        body = rel.parts[1]
        # Each path component must fit the filesystem byte budget.
        assert len(body.encode("utf-8")) <= _MAX_FILENAME_BYTES
        assert body.endswith(".md")
        # The body prefix should still be human-recognisable.
        assert body.startswith(
            "3d0b982edfb41fea27a4e3f4b86fe0598c4947f0@hey.com"
        )

    def test_id_to_rel_path_long_component_is_unique(self):
        # Two distinct long components must produce distinct filenames
        # even when both are truncated.
        base = "thread:" + "x" * 300
        a = _id_to_rel_path(base + "alpha")
        b = _id_to_rel_path(base + "beta")
        assert a != b
        for rel in (a, b):
            for part in rel.parts:
                assert len(part.encode("utf-8")) <= _MAX_FILENAME_BYTES

    def test_id_to_rel_path_no_dangling_percent_escape(self):
        # Build an id that is certain to truncate exactly where a
        # percent-escape would otherwise be sliced in half.  'x' * N
        # followed by a non-ASCII char forces a `%XX` at a controlled
        # offset.
        for pad in range(195, 202):
            doc_id = "x" * pad + "é"  # é encodes to %C3%A9
            rel = _id_to_rel_path(doc_id)
            # No bare trailing '%' in the truncated prefix — the
            # digest suffix always starts with a literal '.'.
            name = rel.parts[-1]
            body = name.removesuffix(".md")
            # Strip the `.{digest}` tail (12 hex chars) to get the prefix.
            prefix = body.rsplit(".", 1)[0]
            assert not prefix.endswith("%")
            assert not (len(prefix) >= 2 and prefix[-2] == "%")
            assert len(name.encode("utf-8")) <= _MAX_FILENAME_BYTES

    def test_id_to_rel_path_http_scheme(self):
        rel = _id_to_rel_path("https://example.com/docs/guide")
        assert rel == Path("https") / "example.com" / "docs" / "guide.md"

    def test_id_to_rel_path_thread_urn_style(self):
        # URN-style scheme (no '//' after the colon) — `thread:` becomes
        # a top-level directory and the body is a single component.
        # `@` is filesystem-safe on every modern OS so it stays literal;
        # `#` is encoded.
        rel = _id_to_rel_path(
            "thread:B1B58B3B-1943-4228-8DC6-4CDDEA5A6B38@northeastern.edu"
            "#185b1a36f1367a087003"
        )
        assert rel == Path("thread") / (
            "B1B58B3B-1943-4228-8DC6-4CDDEA5A6B38@northeastern.edu"
            "%23185b1a36f1367a087003.md"
        )

    def test_id_to_rel_path_mailto(self):
        rel = _id_to_rel_path("mailto:foo@bar.com")
        assert rel == Path("mailto") / "foo@bar.com.md"

    def test_id_to_rel_path_tel(self):
        # '+' is in the safe set so it stays literal.
        rel = _id_to_rel_path("tel:+15551234")
        assert rel == Path("tel") / "+15551234.md"

    def test_id_to_rel_path_bare_scheme_with_slash_body(self):
        # Option B: when a URN-style id's body contains '/', it splits
        # on '/' just like an authority-style URI would.  The fragment
        # marker '#' is not a separator, so '#a' stays glued to the
        # first component (with '#' percent-encoded).
        rel = _id_to_rel_path("thread:UUID@host#a/b/c")
        assert rel == Path("thread") / "UUID@host%23a" / "b" / "c.md"

    def test_id_to_rel_path_simple_colon_id(self):
        # Even ids that "look like tags" get reshaped — but the result
        # is still a sensible browse layout.
        assert _id_to_rel_path("notes:january") == Path("notes") / "january.md"
        assert _id_to_rel_path("topic:rust") == Path("topic") / "rust.md"

    def test_id_to_rel_path_does_not_match_non_uri(self):
        # Leading-dot ids and digit-prefixed ids must not be parsed as
        # schemes (RFC 3986 requires the scheme to start with ALPHA).
        # `.tag/act/commitment` falls through to the '/'-split branch.
        assert _id_to_rel_path(".tag/act/commitment") == Path(".tag/act/commitment.md")
        # A digit-prefixed pseudo-scheme stays flat (encoded).
        assert _id_to_rel_path("2024:01:summary") == Path("2024%3A01%3Asummary.md")

    def test_render_doc_markdown_frontmatter(self):
        doc = {
            "id": "auth-notes",
            "summary": "Body content here.",
            "tags": {"topic": "auth", "_source": "inline"},
            "content_hash": "abc123",
            "content_hash_full": "def456",
            "created_at": "2026-01-15T10:30:00",
            "updated_at": "2026-02-01T14:22:00",
            "accessed_at": "2026-02-19T09:00:00",
        }
        text = _render_doc_markdown(doc)
        assert text.startswith("---\n")
        assert text.endswith("\n")
        # body comes after the closing fence
        parts = text.split("---", 2)
        assert len(parts) == 3
        meta = yaml.safe_load(parts[1])
        assert meta["id"] == "auth-notes"
        assert meta["tags"] == {"topic": "auth", "_source": "inline"}
        assert meta["content_hash"] == "abc123"
        assert meta["content_hash_full"] == "def456"
        assert meta["created_at"] == "2026-01-15T10:30:00"
        assert meta["updated_at"] == "2026-02-01T14:22:00"
        assert meta["accessed_at"] == "2026-02-19T09:00:00"
        assert parts[2].lstrip("\n").rstrip("\n") == "Body content here."

    def test_render_doc_markdown_skips_empty_tags(self):
        doc = {
            "id": "no-tags",
            "summary": "body",
            "tags": {},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        text = _render_doc_markdown(doc)
        meta = yaml.safe_load(text.split("---", 2)[1])
        assert "tags" not in meta

    def test_render_doc_markdown_omits_versions_and_parts(self):
        doc = _make_doc(
            "mixed", "Summary body",
            tags={"topic": "x"},
            versions=[{
                "version": 1, "summary": "old", "tags": {},
                "content_hash": None, "created_at": "2025-12-01T00:00:00",
            }],
            parts=[{
                "part_num": 1, "summary": "p1", "tags": {},
                "created_at": "2026-01-01T00:00:00",
            }],
        )
        text = _render_doc_markdown(doc)
        # Neither versions nor parts should appear anywhere in the markdown.
        assert "versions" not in text
        assert "parts" not in text
        meta = yaml.safe_load(text.split("---", 2)[1])
        assert "versions" not in meta
        assert "parts" not in meta

    def test_write_markdown_export_basic(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("rust-doc", "About Rust", tags={"topic": "rust"}),
            _make_doc("python-doc", "About Python", tags={"topic": "python"}),
        ])
        out = tmp_path / "md-export"
        out.mkdir()

        count, info = _write_markdown_export(keeper, out, include_system=True)
        assert count >= 2

        # One file per user doc (plus any system docs that may exist)
        files = {p.name: p for p in out.iterdir() if p.is_file()}
        assert "rust-doc.md" in files
        assert "python-doc.md" in files

        meta, body = _parse_markdown(files["python-doc.md"])
        assert meta["id"] == "python-doc"
        assert meta["tags"]["topic"] == "python"
        assert "About Python" in body

    def test_write_markdown_export_include_system_false(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("user-doc", "User doc", tags={"topic": "user"}),
            _make_doc(".system-doc", "System doc"),
        ])
        out = tmp_path / "md-user-only"
        out.mkdir()

        count, _info = _write_markdown_export(keeper, out, include_system=False)
        names = {p.name for p in out.rglob("*.md")}
        assert "user-doc.md" in names
        # System docs (dot-prefix ids) must not appear when include_system=False.
        assert ".system-doc.md" not in names
        assert count >= 1

    def test_write_markdown_export_include_system_true(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("user-doc", "User doc"),
            _make_doc(".system-doc", "System doc"),
        ])
        out = tmp_path / "md-with-system"
        out.mkdir()

        _write_markdown_export(keeper, out, include_system=True)
        names = {p.name for p in out.rglob("*.md")}
        assert "user-doc.md" in names
        assert ".system-doc.md" in names

    def test_write_markdown_export_path_like_id(self, keeper, tmp_path):
        file_id = "file:///tmp/readme.md"
        _seed(keeper, [_make_doc(file_id, "# Readme\n\nHello.", tags={"topic": "x"})])
        out = tmp_path / "md-file"
        out.mkdir()

        _write_markdown_export(keeper, out, include_system=False)
        # Expect the note to land at file/tmp/readme.md.md (wget -m style)
        expected = out / "file" / "tmp" / "readme.md.md"
        assert expected.is_file(), f"expected {expected}, got {list(out.rglob('*'))}"

        meta, body = _parse_markdown(expected)
        assert meta["id"] == file_id
        assert "Hello." in body

    def test_write_markdown_export_mixed_hierarchy(self, keeper, tmp_path):
        """Mixed flat, hierarchical, and URI-scheme ids coexist cleanly."""
        _seed(keeper, [
            _make_doc("auth-notes", "body A"),
            _make_doc("notes/2024/jan", "body B"),
            _make_doc("file:///Users/x/readme.md", "body C"),
        ])
        out = tmp_path / "md-mixed"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        assert (out / "auth-notes.md").is_file()
        assert (out / "notes" / "2024" / "jan.md").is_file()
        assert (out / "file" / "Users" / "x" / "readme.md.md").is_file()

    # ------------------------------------------------------------------
    # Parts and versions sidecars
    # ------------------------------------------------------------------

    def test_render_part_markdown_shape(self):
        text = _render_part_markdown("auth-notes", {
            "part_num": 3,
            "summary": "The analysis text for this section.",
            "tags": {"section": "intro"},
            "created_at": "2026-01-15T10:30:00",
        })
        meta = yaml.safe_load(text.split("---", 2)[1])
        body = text.split("---", 2)[2].lstrip("\n").rstrip("\n")
        assert meta["id"] == "auth-notes"
        assert meta["part_num"] == 3
        assert meta["tags"] == {"section": "intro"}
        assert meta["created_at"] == "2026-01-15T10:30:00"
        assert body == "The analysis text for this section."
        assert "summary" not in meta

    def test_render_part_markdown_uses_summary_body(self):
        text = _render_part_markdown("notes", {
            "part_num": 1,
            "summary": "Just the analysis text.",
            "tags": {},
            "created_at": "2026-02-01T12:00:00",
        })
        body = text.split("---", 2)[2].lstrip("\n").rstrip("\n")
        assert body == "Just the analysis text."

    def test_render_version_markdown_shape(self):
        text = _render_version_markdown(
            "auth-notes",
            {
                "version": 7,
                "summary": "Older summary text",
                "tags": {"topic": "auth"},
                "content_hash": "abc123",
                "created_at": "2025-12-01T09:00:00",
            },
            offset=2,
        )
        meta = yaml.safe_load(text.split("---", 2)[1])
        body = text.split("---", 2)[2].lstrip("\n").rstrip("\n")
        # The @V{N} offset (2 = two steps back from current) is stored
        # alongside the absolute database version number for reference.
        assert meta["id"] == "auth-notes"
        assert meta["version_offset"] == 2
        assert meta["version"] == 7
        assert meta["content_hash"] == "abc123"
        assert meta["tags"] == {"topic": "auth"}
        assert body == "Older summary text"

    def test_write_markdown_export_parts_off_by_default(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("with-parts", "Parent summary", parts=[
                {"part_num": 1, "summary": "p1", "tags": {},
                 "created_at": "2026-01-01T00:00:00"},
                {"part_num": 2, "summary": "p2", "tags": {},
                 "created_at": "2026-01-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-no-parts"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        # Parent file present, sidecar dir NOT created (no parts emitted).
        assert (out / "with-parts.md").is_file()
        assert not (out / "with-parts").exists()

    def test_write_markdown_export_parts_sidecar(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("with-parts", "Parent summary", parts=[
                {"part_num": 1, "summary": "Introduction analysis text.",
                 "tags": {"section": "intro"},
                 "created_at": "2026-01-01T00:00:00"},
                {"part_num": 2, "summary": "Body analysis text.",
                 "tags": {"section": "body"},
                 "created_at": "2026-01-02T00:00:00"},
                {"part_num": 5, "summary": "Non-contiguous part text.",
                 "tags": {},
                 "created_at": "2026-01-03T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-parts"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_parts=True,
        )

        # Sidecar dir holds @P{N}.md per part, named after part_num.
        sidecar = out / "with-parts"
        assert sidecar.is_dir()
        assert (sidecar / "@P{1}.md").is_file()
        assert (sidecar / "@P{2}.md").is_file()
        assert (sidecar / "@P{5}.md").is_file()
        # Non-existent part numbers don't get files.
        assert not (sidecar / "@P{3}.md").exists()

        # Each part file's body has the part summary, plus chain
        # navigation links to its parent and to the next part_num
        # neighbour so wiki tools can walk the chain.
        meta, body = _parse_markdown(sidecar / "@P{1}.md")
        assert meta["id"] == "with-parts"
        assert meta["part_num"] == 1
        assert meta["tags"] == {"section": "intro"}
        assert "Introduction analysis text." in body
        # Previous link points back to the parent doc.
        assert "**Previous part:** [with-parts](../with-parts.md)" in body
        # Next link points to the next-higher part_num sidecar.
        assert "**Next part:** [@P{2}](@P%7B2%7D.md)" in body

    def test_write_markdown_export_versions_sidecar_offsets(self, keeper, tmp_path):
        # `versions` in the seed represents archived (non-current) versions.
        # `list_versions` returns them DESC by version number, so the
        # iterator yields them newest-first → @V{1}, @V{2}, @V{3}.
        _seed(keeper, [
            _make_doc("with-history", "Latest summary", versions=[
                {"version": 4, "summary": "Three steps back", "tags": {},
                 "content_hash": "h4", "created_at": "2025-12-04T00:00:00"},
                {"version": 3, "summary": "Two steps back", "tags": {},
                 "content_hash": "h3", "created_at": "2025-12-03T00:00:00"},
                {"version": 2, "summary": "One step back", "tags": {},
                 "content_hash": "h2", "created_at": "2025-12-02T00:00:00"},
                {"version": 1, "summary": "Oldest", "tags": {},
                 "content_hash": "h1", "created_at": "2025-12-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-versions"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_versions=True,
        )

        # Parent file holds the current version; sidecar holds the @V{N}
        # archived versions, where N counts steps back from current.
        assert (out / "with-history.md").is_file()
        sidecar = out / "with-history"
        assert sidecar.is_dir()
        # The export iterator yields archived versions newest-first, so
        # the first one (the most recent prior version, db version=4) is
        # @V{1}, the next (v3) is @V{2}, etc.
        assert (sidecar / "@V{1}.md").is_file()
        assert (sidecar / "@V{2}.md").is_file()
        assert (sidecar / "@V{3}.md").is_file()
        assert (sidecar / "@V{4}.md").is_file()

        meta1, body1 = _parse_markdown(sidecar / "@V{1}.md")
        assert meta1["id"] == "with-history"
        assert meta1["version_offset"] == 1
        assert meta1["version"] == 4  # absolute db version number for reference
        assert "Three steps back" in body1
        # @V{1} is the chain entry from the parent: prev points to the
        # next-older version, next points back to the parent doc.
        assert "**Previous version:** [@V{2}](@V%7B2%7D.md)" in body1
        assert "**Next version:** [with-history](../with-history.md)" in body1

        # Sanity check the deepest archived version maps correctly.
        meta4, body4 = _parse_markdown(sidecar / "@V{4}.md")
        assert meta4["version_offset"] == 4
        assert meta4["version"] == 1
        assert "Oldest" in body4
        # The chain tail has only a "next" link (no older version).
        assert "**Previous version:**" not in body4
        assert "**Next version:** [@V{3}](@V%7B3%7D.md)" in body4

        # The current (latest) version stays in the parent file, NOT in
        # the sidecar — there is no @V{0}.md.
        assert not (sidecar / "@V{0}.md").exists()
        parent_meta, parent_body = _parse_markdown(out / "with-history.md")
        assert "Latest summary" in parent_body
        # Parent doc gets a chain-entry link to @V{1}.
        assert (
            "**Previous version:** [@V{1}](with-history/@V%7B1%7D.md)"
            in parent_body
        )

    def test_write_markdown_export_parts_and_versions_combined(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("combo", "current", parts=[
                {"part_num": 1, "summary": "part one text", "tags": {},
                 "created_at": "2026-01-01T00:00:00"},
            ], versions=[
                {"version": 1, "summary": "old", "tags": {},
                 "content_hash": None, "created_at": "2025-12-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-combo"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False,
            include_parts=True, include_versions=True,
        )

        sidecar = out / "combo"
        assert (out / "combo.md").is_file()
        assert (sidecar / "@P{1}.md").is_file()
        assert (sidecar / "@V{1}.md").is_file()

    def test_write_markdown_export_no_sidecar_when_no_parts_or_versions(self, keeper, tmp_path):
        # Notes that have no parts/versions get no sidecar dir even when
        # both flags are on.
        _seed(keeper, [_make_doc("plain", "just a summary")])
        out = tmp_path / "md-plain"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False,
            include_parts=True, include_versions=True,
        )
        assert (out / "plain.md").is_file()
        assert not (out / "plain").exists()

    def test_write_markdown_export_case_fold_file_vs_sidecar_dir(
        self, keeper, tmp_path,
    ):
        """File from one id auto-disambiguates from sidecar dir of another.

        Reproduces a bug where two file:// ids whose paths case-fold
        to the same on-disk slot crashed with a raw FileExistsError on
        case-insensitive filesystems when the sidecar dir creation
        tried to overwrite an existing file:

            ``file:///abc/def``    → file/abc/def.md       (file)
            ``file:///abc/DEF.md`` → file/abc/DEF.md.md    (file)
                                     file/abc/DEF.md/      (sidecar dir)

        On macOS APFS the sidecar dir ``file/abc/DEF.md`` case-folds
        to ``file/abc/def.md`` — already a regular file from the
        first id.  The export now appends a hash suffix to the
        second doc's stem so both notes coexist on disk and the
        sidecar dir lives at a non-colliding path.
        """
        _seed(keeper, [
            _make_doc("file:///abc/def", "A body"),
            _make_doc("file:///abc/DEF.md", "B body", parts=[
                {"part_num": 1, "summary": "p1", "tags": {},
                 "created_at": "2026-01-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-casefold"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_parts=True,
        )

        # The first doc landed at the canonical path.
        canonical_a = out / "file" / "abc" / "def.md"
        assert canonical_a.is_file()

        # The second doc was disambiguated — its main file is no
        # longer at ``DEF.md.md`` but at ``DEF.md.<hash>.md``, and
        # its sidecar dir is at ``DEF.md.<hash>/``.  Find them.
        b_files = [
            p for p in (out / "file" / "abc").iterdir()
            if p.is_file() and p != canonical_a
        ]
        assert len(b_files) == 1, f"expected one B file, got {b_files}"
        b_main = b_files[0]
        assert b_main.name.startswith("DEF.md.")
        assert b_main.name.endswith(".md")
        # The disambiguation hash is 8 hex chars wedged into the stem.
        # The frontmatter still records the canonical id.
        meta_b, _ = _parse_markdown(b_main)
        assert meta_b["id"] == "file:///abc/DEF.md"

        # The sidecar dir matches the disambiguated stem (so its case-
        # folded name no longer collides with ``def.md``) and the part
        # file lives inside it.
        b_sidecar = b_main.with_suffix("")
        assert b_sidecar.is_dir()
        assert (b_sidecar / "@P{1}.md").is_file()

    def test_write_markdown_export_case_fold_two_main_files(
        self, keeper, tmp_path,
    ):
        """Two main-file paths that differ only in case auto-disambiguate."""
        _seed(keeper, [
            _make_doc("readme", "lower"),
            _make_doc("README", "upper"),
        ])
        out = tmp_path / "md-case-files"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        files = sorted(p.name for p in out.iterdir() if p.is_file())
        # Two distinct on-disk files exist — one canonical, one with
        # an 8-hex-char disambiguation suffix.
        assert len(files) == 2
        canonical = {"readme.md", "README.md"}
        disambiguated = [f for f in files if f not in canonical]
        canonical_files = [f for f in files if f in canonical]
        assert len(canonical_files) == 1
        assert len(disambiguated) == 1
        # The disambiguated file's stem is `<canonical_stem>.<hash>`.
        assert disambiguated[0].count(".") == 2

        # Both notes' frontmatter still records the canonical id —
        # the disambiguation is purely an on-disk detail.
        ids = set()
        for f in files:
            meta, _ = _parse_markdown(out / f)
            ids.add(meta["id"])
        assert ids == {"readme", "README"}

    def test_write_markdown_export_disambiguation_in_inverse_edge_links(
        self, keeper, tmp_path,
    ):
        """Inverse-edge links target the disambiguated path, not canonical.

        When a doc with incoming inverse edges is itself disambiguated,
        the ``## Referenced By`` section on a third doc that points at
        it must use the disambiguated filename — otherwise wiki tools
        follow the link to a file that doesn't exist.
        """
        _seed(keeper, [
            _make_doc("readme", "lower"),
            _make_doc("README", "upper"),
            _make_doc("source", "Source body"),
        ])
        # 'source' has an edge pointing at 'readme' (the lowercase one).
        ds = keeper._document_store
        coll = keeper._resolve_doc_collection()
        ds.upsert_edge(
            coll, "source", "speaker", "readme", "said",
            "2026-01-15T10:00:00",
        )

        out = tmp_path / "md-disambig-link"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        # Find which file is the canonical 'readme' and which is the
        # disambiguated 'README'.  The inverse-edge link in source.md
        # must point to whichever file is the actual on-disk path of
        # the lowercase 'readme' doc.
        readme_file: Optional[Path] = None
        for p in out.iterdir():
            if not p.is_file() or p.name == "source.md":
                continue
            meta, _ = _parse_markdown(p)
            if meta["id"] == "readme":
                readme_file = p
                break
        assert readme_file is not None

        # The link target in source.md should resolve (after URL-decoding)
        # to the actual on-disk filename of 'readme'.
        from urllib.parse import unquote
        _meta, source_body = _parse_markdown(out / "source.md")
        assert "## Referenced By" not in source_body  # source has no incoming
        # 'readme' has the incoming edge — its body has the section.
        _meta, readme_body = _parse_markdown(readme_file)
        assert "## Referenced By" in readme_body
        assert "[source](source.md)" in readme_body

        # Now the more interesting check: source.md does NOT have an
        # outbound link rendered (forward edges are 'just tags'), but
        # readme.md's section uses the live edges table.  The hash-
        # disambiguated 'README' doc shouldn't appear in any section.
        for p in out.iterdir():
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            # No broken links to the canonical 'README.md' which may
            # not exist on disk if it got disambiguated.
            for line in text.split("\n"):
                if "](" not in line:
                    continue
                start = line.index("](") + 2
                end = line.index(")", start)
                target = line[start:end]
                if target.startswith(("http://", "https://")):
                    continue
                resolved = unquote(target)
                # Resolve relative to this file's directory.
                src_dir = p.parent
                target_path = (src_dir / resolved).resolve()
                assert target_path.exists() or target_path.is_symlink(), (
                    f"broken link in {p.name}: {target} → {target_path}"
                )

    def test_cli_include_parts_and_versions_flags(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(app, ["data", "export", "--help"])
        assert r.exit_code == 0
        assert "--include-parts" in r.output
        assert "--include-versions" in r.output

    def test_cli_rejects_include_parts_with_json(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(
            app,
            ["--store", str(tmp_path), "data", "export", "out.json",
             "--include-parts"],
        )
        assert r.exit_code != 0
        assert "only applies to --format md" in r.output

    def test_cli_rejects_include_versions_with_json(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(
            app,
            ["--store", str(tmp_path), "data", "export", "out.json",
             "--include-versions"],
        )
        assert r.exit_code != 0
        assert "only applies to --format md" in r.output

    def test_cli_rejects_stdout_for_markdown(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(
            app, ["--store", str(tmp_path), "data", "export", "-", "--format", "md"],
        )
        assert r.exit_code != 0
        assert "markdown export requires a directory" in r.output

    def test_cli_rejects_nonempty_markdown_dir(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        out = tmp_path / "nonempty"
        out.mkdir()
        (out / "junk.txt").write_text("x")
        r = CliRunner().invoke(
            app, ["--store", str(tmp_path), "data", "export", str(out), "--format", "md"],
        )
        assert r.exit_code != 0
        assert "not empty" in r.output

    def test_cli_rejects_unknown_format(self, tmp_path):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(
            app, ["--store", str(tmp_path), "data", "export", "foo", "--format", "xml"],
        )
        assert r.exit_code != 0
        assert "must be 'json' or 'md'" in r.output

    def test_cli_help_shows_include_system_flag(self):
        from typer.testing import CliRunner
        from keep.cli_app import app
        r = CliRunner().invoke(app, ["data", "export", "--help"])
        assert r.exit_code == 0
        assert "--include-system" in r.output
        # Old --exclude-system flag must be gone.
        assert "--exclude-system" not in r.output

    def test_write_markdown_export_skips_versions_and_parts(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc(
                "with-history", "Latest summary",
                versions=[{
                    "version": 1, "summary": "Old summary", "tags": {},
                    "content_hash": None, "created_at": "2025-12-01T00:00:00",
                }],
                parts=[{
                    "part_num": 1, "summary": "p1", "tags": {},
                    "created_at": "2026-01-01T00:00:00",
                }],
            ),
        ])
        out = tmp_path / "md-history"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        text = (out / "with-history.md").read_text(encoding="utf-8")
        assert "Latest summary" in text
        # Old version bodies must not leak into the export.
        assert "Old summary" not in text

    def test_write_markdown_export_progress_callback(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("a", "first note"),
            _make_doc("b", "second note"),
            _make_doc("c", "third note"),
        ])
        out = tmp_path / "md-progress"
        out.mkdir()

        events: list[tuple[int, int, str]] = []
        _write_markdown_export(
            keeper, out, include_system=False,
            progress=lambda cur, total, label: events.append((cur, total, label)),
        )

        # One callback per exported note, counter strictly increasing from 1..N.
        assert len(events) == 3
        assert [e[0] for e in events] == [1, 2, 3]
        # Total is stable across calls and matches the number of notes.
        assert {e[1] for e in events} == {3}
        # Labels are note ids that were just written.
        assert {e[2] for e in events} == {"a", "b", "c"}

    # ------------------------------------------------------------------
    # Linear-chain navigation between parent and sidecars
    # ------------------------------------------------------------------

    def test_md_link_target_same_dir(self):
        # note.md → other.md (root to root): no '..' prefix.
        assert _md_link_target(Path("other.md"), Path("note.md")) == "other.md"

    def test_md_link_target_into_subdir(self):
        # note.md → note/@P{1}.md: relative descend, '{}' percent-encoded.
        assert (
            _md_link_target(
                Path("note") / "@P{1}.md",
                Path("note.md"),
            )
            == "note/@P%7B1%7D.md"
        )

    def test_md_link_target_back_to_parent(self):
        # note/@P{1}.md → note.md: one '..' segment.
        assert (
            _md_link_target(
                Path("note.md"),
                Path("note") / "@P{1}.md",
            )
            == "../note.md"
        )

    def test_md_link_target_sibling_in_same_subdir(self):
        # note/@P{1}.md → note/@P{2}.md: same dir, no traversal.
        assert (
            _md_link_target(
                Path("note") / "@P{2}.md",
                Path("note") / "@P{1}.md",
            )
            == "@P%7B2%7D.md"
        )

    def test_md_link_target_nested_root(self):
        # notes/2024/jan.md → notes/2024/jan/@P{1}.md.
        assert (
            _md_link_target(
                Path("notes") / "2024" / "jan" / "@P{1}.md",
                Path("notes") / "2024" / "jan.md",
            )
            == "jan/@P%7B1%7D.md"
        )

    def test_md_link_target_nested_back_to_root(self):
        # notes/2024/jan/@P{1}.md → notes/2024/jan.md: '../jan.md'.
        assert (
            _md_link_target(
                Path("notes") / "2024" / "jan.md",
                Path("notes") / "2024" / "jan" / "@P{1}.md",
            )
            == "../jan.md"
        )

    def test_md_link_target_across_dirs(self):
        # nested → root sibling: traversal up + down.
        assert (
            _md_link_target(
                Path("Deborah.md"),
                Path("notes") / "2024" / "jan.md",
            )
            == "../../Deborah.md"
        )

    def test_md_link_target_url_encodes_parens(self):
        # On-disk component with '()' must percent-encode in the link
        # target — bare parens would otherwise unbalance markdown's
        # link-URL syntax.
        assert (
            _md_link_target(
                Path("foo(bar).md"),
                Path("here.md"),
            )
            == "foo%28bar%29.md"
        )

    def test_md_link_target_double_encodes_literal_percent(self):
        # `_id_to_rel_path` already percent-encodes filesystem-unsafe
        # chars inside each path component, so '#' lands on disk as
        # the LITERAL bytes '%23' inside the filename.  For a wiki
        # tool to URL-decode a markdown link target back to that
        # literal filename, every '%' has to be re-encoded as '%25'
        # — otherwise '%23' in the link decodes to '#' and resolves
        # to nothing on disk.
        rel = _id_to_rel_path("thread:abc@host#frag")
        # Sanity: the on-disk component carries the percent-escape.
        assert "%23frag" in rel.parts[-1]
        link = _md_link_target(rel, Path("anchor.md"))
        # '%23' must be re-encoded as '%2523' so URL-decoding it gives
        # back the literal '%23' that exists on disk.
        assert "%2523frag" in link

    def test_md_link_target_round_trips_to_on_disk_filename(self):
        # End-to-end check of the encode/decode round trip: the URL
        # produced by _md_link_target must, after URL-decoding,
        # exactly match the on-disk relative path.
        from urllib.parse import unquote
        for doc_id in (
            "auth-notes",
            "notes/2024/jan",
            "file:///Users/x/README.md",
            "thread:abc@host#frag",
            "%9f86d081884c",
        ):
            rel = _id_to_rel_path(doc_id)
            link = _md_link_target(rel, Path("anchor.md"))
            assert unquote(link) == rel.as_posix(), (
                f"round-trip failed for {doc_id!r}: "
                f"link={link!r} decoded={unquote(link)!r} "
                f"on-disk={rel.as_posix()!r}"
            )

    def test_parts_chain_links_non_contiguous(self, keeper, tmp_path):
        """Parts chain follows part_num order even with gaps."""
        _seed(keeper, [
            _make_doc("doc", "Parent body", parts=[
                {"part_num": 1, "summary": "first part",
                 "tags": {}, "created_at": "2026-01-01T00:00:00"},
                {"part_num": 2, "summary": "second part",
                 "tags": {}, "created_at": "2026-01-02T00:00:00"},
                {"part_num": 5, "summary": "fifth part",
                 "tags": {}, "created_at": "2026-01-03T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-parts-chain"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_parts=True,
        )

        # Parent links forward to the first part in the chain.
        _meta, parent_body = _parse_markdown(out / "doc.md")
        assert "**Next part:** [@P{1}](doc/@P%7B1%7D.md)" in parent_body
        # Parent doesn't have any "previous part" line.
        assert "**Previous part:**" not in parent_body

        # Chain head: prev → parent, next → @P{2}
        _m, b1 = _parse_markdown(out / "doc" / "@P{1}.md")
        assert "**Previous part:** [doc](../doc.md)" in b1
        assert "**Next part:** [@P{2}](@P%7B2%7D.md)" in b1
        assert "first part" in b1

        # Chain middle: prev → @P{1}, next → @P{5}
        _m, b2 = _parse_markdown(out / "doc" / "@P{2}.md")
        assert "**Previous part:** [@P{1}](@P%7B1%7D.md)" in b2
        assert "**Next part:** [@P{5}](@P%7B5%7D.md)" in b2
        assert "second part" in b2

        # Chain tail: prev → @P{2}, no next link.
        _m, b5 = _parse_markdown(out / "doc" / "@P{5}.md")
        assert "**Previous part:** [@P{2}](@P%7B2%7D.md)" in b5
        assert "**Next part:**" not in b5
        assert "fifth part" in b5

    def test_versions_chain_links(self, keeper, tmp_path):
        """Versions chain follows @V{N} offset order."""
        _seed(keeper, [
            _make_doc("doc", "current", versions=[
                {"version": 4, "summary": "v4 (one back)",
                 "tags": {}, "content_hash": "h4",
                 "created_at": "2025-12-04T00:00:00"},
                {"version": 3, "summary": "v3 (two back)",
                 "tags": {}, "content_hash": "h3",
                 "created_at": "2025-12-03T00:00:00"},
                {"version": 2, "summary": "v2 (three back)",
                 "tags": {}, "content_hash": "h2",
                 "created_at": "2025-12-02T00:00:00"},
                {"version": 1, "summary": "v1 (four back)",
                 "tags": {}, "content_hash": "h1",
                 "created_at": "2025-12-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-versions-chain"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_versions=True,
        )

        # Parent links back to its most-recent prior version (@V{1}).
        _m, parent_body = _parse_markdown(out / "doc.md")
        assert "**Previous version:** [@V{1}](doc/@V%7B1%7D.md)" in parent_body
        assert "**Next version:**" not in parent_body
        assert "current" in parent_body

        # Chain head: prev → @V{2}, next → parent
        _m, b1 = _parse_markdown(out / "doc" / "@V{1}.md")
        assert "**Previous version:** [@V{2}](@V%7B2%7D.md)" in b1
        assert "**Next version:** [doc](../doc.md)" in b1

        # Middle: prev → @V{3}, next → @V{1}
        _m, b2 = _parse_markdown(out / "doc" / "@V{2}.md")
        assert "**Previous version:** [@V{3}](@V%7B3%7D.md)" in b2
        assert "**Next version:** [@V{1}](@V%7B1%7D.md)" in b2

        # Chain tail (oldest): no prev, next → @V{3}
        _m, b4 = _parse_markdown(out / "doc" / "@V{4}.md")
        assert "**Previous version:**" not in b4
        assert "**Next version:** [@V{3}](@V%7B3%7D.md)" in b4

    def test_parent_doc_chain_entries_for_both_parts_and_versions(
        self, keeper, tmp_path,
    ):
        """A doc with parts AND versions has two chain-entry links."""
        _seed(keeper, [
            _make_doc("doc", "current", parts=[
                {"part_num": 1, "summary": "p1",
                 "tags": {}, "created_at": "2026-01-01T00:00:00"},
            ], versions=[
                {"version": 1, "summary": "older",
                 "tags": {}, "content_hash": "h1",
                 "created_at": "2025-12-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-both"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False,
            include_parts=True, include_versions=True,
        )
        _m, parent_body = _parse_markdown(out / "doc.md")
        assert "**Previous version:** [@V{1}](doc/@V%7B1%7D.md)" in parent_body
        assert "**Next part:** [@P{1}](doc/@P%7B1%7D.md)" in parent_body

    def test_doc_with_no_sidecars_has_no_chain_links(self, keeper, tmp_path):
        """A plain doc gets no chain navigation in its body."""
        _seed(keeper, [_make_doc("plain", "Just a summary")])
        out = tmp_path / "md-plain-nav"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)
        _m, body = _parse_markdown(out / "plain.md")
        assert "**Previous version:**" not in body
        assert "**Next part:**" not in body

    def test_chain_links_omitted_when_flags_off(self, keeper, tmp_path):
        """Sidecars must not be linked from the parent when their flag is off."""
        _seed(keeper, [
            _make_doc("doc", "current", parts=[
                {"part_num": 1, "summary": "p1",
                 "tags": {}, "created_at": "2026-01-01T00:00:00"},
            ], versions=[
                {"version": 1, "summary": "old",
                 "tags": {}, "content_hash": None,
                 "created_at": "2025-12-01T00:00:00"},
            ]),
        ])
        out = tmp_path / "md-flags-off"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)
        _m, body = _parse_markdown(out / "doc.md")
        assert "**Previous version:**" not in body
        assert "**Next part:**" not in body

    # ------------------------------------------------------------------
    # Inverse-edge "Referenced By" sections
    # ------------------------------------------------------------------

    def test_parent_inverse_edges_section(self, keeper, tmp_path):
        """Parent doc gets a Referenced By section from current edges."""
        _seed(keeper, [
            _make_doc("conv1", "First conversation"),
            _make_doc("conv2", "Second conversation"),
            _make_doc("Deborah", "About Deborah"),
        ])
        # Insert two inverse edges pointing at "Deborah" — both with
        # the inverse predicate "said" (the bundled `speaker` tag's
        # _inverse).  Bypassing the tagdoc machinery is fine here:
        # the markdown export queries the edges table directly.
        ds = keeper._document_store
        coll = keeper._resolve_doc_collection()
        ds.upsert_edge(
            coll, "conv1", "speaker", "Deborah", "said",
            "2026-01-15T10:00:00",
        )
        ds.upsert_edge(
            coll, "conv2", "speaker", "Deborah", "said",
            "2026-01-16T10:00:00",
        )

        out = tmp_path / "md-inverse"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        _meta, body = _parse_markdown(out / "Deborah.md")
        # Section header
        assert "## Referenced By" in body
        # Inverse predicate label
        assert "- **said:**" in body
        # Both source links present, with relative paths back to the
        # source notes (in the same root dir, so no '..' traversal).
        assert "[conv1](conv1.md)" in body
        assert "[conv2](conv2.md)" in body

        # Source docs themselves don't have a Referenced By section
        # (nothing points at them).
        _m, conv1_body = _parse_markdown(out / "conv1.md")
        assert "## Referenced By" not in conv1_body

    def test_parent_inverse_edges_skips_system_sources(self, keeper, tmp_path):
        """When include_system=False, system-id edge sources are filtered out."""
        _seed(keeper, [_make_doc("target", "Target body")])
        ds = keeper._document_store
        coll = keeper._resolve_doc_collection()
        ds.upsert_edge(
            coll, ".meta/something", "speaker", "target", "said",
            "2026-01-15T10:00:00",
        )
        ds.upsert_edge(
            coll, "real-source", "speaker", "target", "said",
            "2026-01-16T10:00:00",
        )

        out = tmp_path / "md-sys-filter"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        _m, body = _parse_markdown(out / "target.md")
        assert "[real-source](real-source.md)" in body
        # System-id source is not rendered as a link.
        assert ".meta/something" not in body

    def test_version_sidecar_inverse_edges(self, keeper, tmp_path):
        """Version sidecars expose inverse edges from version_edges."""
        # Seed both the target doc (with archived versions) and a
        # source doc with versions whose tags reference the target.
        # `import_batch` calls _rebuild_version_edges_for_source so
        # version_edges gets populated automatically — but only if a
        # tagdoc with _inverse exists at import time, so include one
        # in the same batch.
        _seed(keeper, [
            {
                "id": ".tag/speaker",
                "summary": "speaker tagdoc",
                "tags": {"_inverse": "said"},
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:00",
                "accessed_at": "2025-01-01T00:00:00",
            },
            _make_doc("target", "Target body", versions=[
                {"version": 1, "summary": "older target body",
                 "tags": {}, "content_hash": "ht1",
                 "created_at": "2025-12-01T00:00:00"},
            ]),
            _make_doc(
                "src",
                "current source",
                tags={"speaker": "target"},
                versions=[
                    {"version": 1,
                     "summary": "older source body",
                     "tags": {"speaker": "target"},
                     "content_hash": "hs1",
                     "created_at": "2025-12-01T00:00:00"},
                ],
            ),
        ])

        out = tmp_path / "md-version-inverse"
        out.mkdir()
        _write_markdown_export(
            keeper, out, include_system=False, include_versions=True,
        )

        # The single archived version sidecar of `target` should
        # surface the historical reference from `src` (through
        # version_edges).  The link points back from inside the
        # sidecar dir to the source doc at the export root.
        _meta, body = _parse_markdown(out / "target" / "@V{1}.md")
        assert "## Referenced By" in body
        assert "- **said:**" in body
        assert "[src](../src.md)" in body
