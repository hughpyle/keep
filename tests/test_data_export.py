"""Tests for keep data export/import."""

import json
import pytest
import yaml
from pathlib import Path

from keep.api import Keeper
from keep.cli_app import (
    _MAX_FILENAME_BYTES,
    _ExportCollisionError,
    _id_to_rel_path,
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
                "content": "chunk text", "created_at": "2026-01-01T00:00:00",
            }],
        )
        text = _render_doc_markdown(doc)
        # Neither versions nor parts should appear anywhere in the markdown.
        assert "versions" not in text
        assert "parts" not in text
        assert "chunk text" not in text
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
        # The part's text lives in `summary` (same as for notes); the
        # `content` field on the parts table is vestigial and the
        # export deliberately ignores it.
        text = _render_part_markdown("auth-notes", {
            "part_num": 3,
            "summary": "The analysis text for this section.",
            "tags": {"section": "intro"},
            "content": "ignored vestigial column",
            "created_at": "2026-01-15T10:30:00",
        })
        meta = yaml.safe_load(text.split("---", 2)[1])
        body = text.split("---", 2)[2].lstrip("\n").rstrip("\n")
        assert meta["id"] == "auth-notes"
        assert meta["part_num"] == 3
        assert meta["tags"] == {"section": "intro"}
        assert meta["created_at"] == "2026-01-15T10:30:00"
        # Body comes from `summary`; `content` does NOT appear anywhere.
        assert body == "The analysis text for this section."
        assert "summary" not in meta
        assert "content" not in meta
        assert "ignored vestigial column" not in text

    def test_render_part_markdown_ignores_content_when_summary_only(self):
        # Real-world case: 99.4% of parts in actual stores have
        # summary populated and content empty.  The body must come
        # from summary.
        text = _render_part_markdown("notes", {
            "part_num": 1,
            "summary": "Just the analysis text.",
            "tags": {},
            "content": "",
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
                 "content": "first chunk", "created_at": "2026-01-01T00:00:00"},
                {"part_num": 2, "summary": "p2", "tags": {},
                 "content": "second chunk", "created_at": "2026-01-01T00:00:00"},
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
                 "tags": {"section": "intro"}, "content": "",
                 "created_at": "2026-01-01T00:00:00"},
                {"part_num": 2, "summary": "Body analysis text.",
                 "tags": {"section": "body"}, "content": "",
                 "created_at": "2026-01-02T00:00:00"},
                {"part_num": 5, "summary": "Non-contiguous part text.",
                 "tags": {}, "content": "",
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

        # Each part file's body is the part summary (the part's text);
        # the parent id is in frontmatter; tags are preserved.
        meta, body = _parse_markdown(sidecar / "@P{1}.md")
        assert meta["id"] == "with-parts"
        assert meta["part_num"] == 1
        assert meta["tags"] == {"section": "intro"}
        assert body.strip() == "Introduction analysis text."

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
        assert body1.strip() == "Three steps back"

        # Sanity check the deepest archived version maps correctly.
        meta4, body4 = _parse_markdown(sidecar / "@V{4}.md")
        assert meta4["version_offset"] == 4
        assert meta4["version"] == 1
        assert body4.strip() == "Oldest"

        # The current (latest) version stays in the parent file, NOT in
        # the sidecar — there is no @V{0}.md.
        assert not (sidecar / "@V{0}.md").exists()
        parent_meta, parent_body = _parse_markdown(out / "with-history.md")
        assert parent_body.strip() == "Latest summary"

    def test_write_markdown_export_parts_and_versions_combined(self, keeper, tmp_path):
        _seed(keeper, [
            _make_doc("combo", "current", parts=[
                {"part_num": 1, "summary": "part one text", "tags": {},
                 "content": "", "created_at": "2026-01-01T00:00:00"},
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

    def test_write_markdown_export_collision_detected(self, tmp_path):
        # The db enforces unique ids, but the encoded-path layer can
        # in principle collide for ids that differ only in characters
        # the encoder collapses.  We test the detection logic directly
        # by feeding a fake iterator that yields two distinct entries
        # mapping to the same target path.
        out = tmp_path / "md-collide"
        out.mkdir()

        class _FakeKeeper:
            def export_iter(self, *, include_system):
                yield {
                    "format": "keep-export", "version": 1,
                    "exported_at": "2026-01-01T00:00:00",
                    "store_info": {"document_count": 2, "version_count": 0,
                                   "part_count": 0, "collection": "default"},
                }
                for summary in ("first", "second"):
                    yield {
                        "id": "dup", "summary": summary, "tags": {},
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                        "accessed_at": "2026-01-01T00:00:00",
                    }

        with pytest.raises(_ExportCollisionError) as ei:
            _write_markdown_export(_FakeKeeper(), out, include_system=False)
        assert ei.value.path == Path("dup.md")
        assert ei.value.first_id == "dup"
        assert ei.value.second_id == "dup"

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
                    "content": "part body content", "created_at": "2026-01-01T00:00:00",
                }],
            ),
        ])
        out = tmp_path / "md-history"
        out.mkdir()
        _write_markdown_export(keeper, out, include_system=False)

        text = (out / "with-history.md").read_text(encoding="utf-8")
        assert "Latest summary" in text
        # Old version content and part content must not leak into the export.
        assert "Old summary" not in text
        assert "part body content" not in text

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
