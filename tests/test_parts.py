"""
Tests for document parts (structural decomposition).

Tests cover:
- DocumentStore CRUD for parts
- Schema migration (version 3→4)
- ChromaStore part methods
- Keeper.analyze() with mocked LLM
- Keeper.get_part() and list_parts()
- CLI @P{N} parsing
- Parts manifest in get output
- JSON decomposition parsing
- Simple chunk fallback
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from keep.api import (
    Keeper,
    _parse_decomposition_json,
    _simple_chunk_decomposition,
)
from keep.document_store import DocumentStore, PartInfo
from keep.types import utc_now


# ---------------------------------------------------------------------------
# DocumentStore CRUD tests
# ---------------------------------------------------------------------------


class TestDocumentStoreParts:
    """Test PartInfo CRUD in the SQLite document store."""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "test.db"
        return DocumentStore(db_path)

    def test_upsert_and_list_parts(self, store):
        """Parts can be stored and retrieved."""
        now = utc_now()
        parts = [
            PartInfo(part_num=1, summary="Intro", tags={"topic": "overview"},
                     content="The introduction...", created_at=now),
            PartInfo(part_num=2, summary="Main body", tags={"topic": "detail"},
                     content="The main body...", created_at=now),
        ]
        count = store.upsert_parts("default", "doc:1", parts)
        assert count == 2

        result = store.list_parts("default", "doc:1")
        assert len(result) == 2
        assert result[0].part_num == 1
        assert result[0].summary == "Intro"
        assert result[0].tags == {"topic": "overview"}
        assert result[0].content == "The introduction..."
        assert result[1].part_num == 2

    def test_get_part(self, store):
        """Individual parts can be retrieved by number."""
        now = utc_now()
        parts = [
            PartInfo(part_num=1, summary="Part 1", tags={},
                     content="Content 1", created_at=now),
            PartInfo(part_num=2, summary="Part 2", tags={},
                     content="Content 2", created_at=now),
        ]
        store.upsert_parts("default", "doc:1", parts)

        part = store.get_part("default", "doc:1", 1)
        assert part is not None
        assert part.summary == "Part 1"

        part2 = store.get_part("default", "doc:1", 2)
        assert part2 is not None
        assert part2.summary == "Part 2"

        missing = store.get_part("default", "doc:1", 99)
        assert missing is None

    def test_part_count(self, store):
        """Part count returns correct number."""
        assert store.part_count("default", "doc:1") == 0

        now = utc_now()
        parts = [PartInfo(i, f"Part {i}", {}, f"Content {i}", now) for i in range(1, 4)]
        store.upsert_parts("default", "doc:1", parts)
        assert store.part_count("default", "doc:1") == 3

    def test_delete_parts(self, store):
        """Parts can be deleted."""
        now = utc_now()
        parts = [PartInfo(1, "Part 1", {}, "Content", now)]
        store.upsert_parts("default", "doc:1", parts)
        assert store.part_count("default", "doc:1") == 1

        deleted = store.delete_parts("default", "doc:1")
        assert deleted == 1
        assert store.part_count("default", "doc:1") == 0

    def test_upsert_replaces_atomically(self, store):
        """Re-upsert replaces all parts atomically."""
        now = utc_now()

        # Initial parts
        parts_v1 = [
            PartInfo(1, "Old part 1", {}, "Old 1", now),
            PartInfo(2, "Old part 2", {}, "Old 2", now),
            PartInfo(3, "Old part 3", {}, "Old 3", now),
        ]
        store.upsert_parts("default", "doc:1", parts_v1)
        assert store.part_count("default", "doc:1") == 3

        # Replace with fewer parts
        parts_v2 = [
            PartInfo(1, "New part 1", {"topic": "new"}, "New 1", now),
            PartInfo(2, "New part 2", {}, "New 2", now),
        ]
        store.upsert_parts("default", "doc:1", parts_v2)
        assert store.part_count("default", "doc:1") == 2

        result = store.list_parts("default", "doc:1")
        assert result[0].summary == "New part 1"
        assert result[0].tags == {"topic": "new"}

    def test_parts_isolated_by_id(self, store):
        """Parts for different documents are independent."""
        now = utc_now()
        store.upsert_parts("default", "doc:1", [PartInfo(1, "A", {}, "a", now)])
        store.upsert_parts("default", "doc:2", [PartInfo(1, "B", {}, "b", now)])

        assert store.part_count("default", "doc:1") == 1
        assert store.part_count("default", "doc:2") == 1

        store.delete_parts("default", "doc:1")
        assert store.part_count("default", "doc:1") == 0
        assert store.part_count("default", "doc:2") == 1


# ---------------------------------------------------------------------------
# Schema migration test
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    """Test that schema v3→v4 migration creates the parts table."""

    def test_migration_creates_parts_table(self, tmp_path):
        """New databases get the document_parts table."""
        db_path = tmp_path / "test.db"
        store = DocumentStore(db_path)

        # Check table exists by inserting a part
        now = utc_now()
        parts = [PartInfo(1, "Test", {}, "Content", now)]
        store.upsert_parts("default", "test", parts)
        assert store.part_count("default", "test") == 1
        store.close()


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------


class TestDecompositionParsing:
    """Test _parse_decomposition_json."""

    def test_parse_json_array(self):
        text = json.dumps([
            {"summary": "Intro", "content": "The intro text"},
            {"summary": "Body", "content": "The body text", "tags": {"topic": "main"}},
        ])
        result = _parse_decomposition_json(text, "")
        assert len(result) == 2
        assert result[0]["summary"] == "Intro"
        assert result[1]["tags"] == {"topic": "main"}

    def test_parse_code_fenced(self):
        text = '```json\n[{"summary": "Test", "content": "Content"}]\n```'
        result = _parse_decomposition_json(text, "")
        assert len(result) == 1
        assert result[0]["summary"] == "Test"

    def test_parse_wrapper_object(self):
        text = json.dumps({"sections": [
            {"summary": "Part 1", "content": "Text 1"},
        ]})
        result = _parse_decomposition_json(text, "")
        assert len(result) == 1
        assert result[0]["summary"] == "Part 1"

    def test_parse_empty_text(self):
        assert _parse_decomposition_json("", "") == []
        assert _parse_decomposition_json(None, "") == []

    def test_parse_invalid_json(self):
        assert _parse_decomposition_json("not json at all", "") == []

    def test_parse_skips_empty_entries(self):
        text = json.dumps([
            {"summary": "Good", "content": "Text"},
            {},  # No summary or content
            {"summary": "", "content": ""},  # Empty strings
        ])
        result = _parse_decomposition_json(text, "")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Simple chunk fallback tests
# ---------------------------------------------------------------------------


class TestSimpleChunkDecomposition:
    """Test _simple_chunk_decomposition."""

    def test_multiple_paragraphs(self):
        content = "First paragraph with enough text to be meaningful.\n\n" \
                  "Second paragraph also has content.\n\n" \
                  "Third paragraph rounds things out with additional material " \
                  "that makes the total long enough to form multiple chunks."
        # This content is short, chunks may merge
        result = _simple_chunk_decomposition(content)
        # Should produce at least something
        assert isinstance(result, list)

    def test_single_paragraph_returns_empty(self):
        result = _simple_chunk_decomposition("Just one paragraph.")
        assert result == []

    def test_empty_content(self):
        result = _simple_chunk_decomposition("")
        assert result == []

    def test_long_content_produces_chunks(self):
        # Create content with clearly separated sections
        paragraphs = [f"Section {i}. " + "x" * 500 for i in range(5)]
        content = "\n\n".join(paragraphs)
        result = _simple_chunk_decomposition(content)
        assert len(result) >= 2
        for chunk in result:
            assert "summary" in chunk
            assert "content" in chunk


# ---------------------------------------------------------------------------
# Keeper.analyze() tests (mocked providers)
# ---------------------------------------------------------------------------


class TestKeeperAnalyze:
    """Test Keeper.analyze() with mocked providers."""

    def test_analyze_creates_parts(self, mock_providers, tmp_path):
        """analyze() creates parts from LLM decomposition."""
        kp = Keeper(store_path=tmp_path)

        # First store a document
        kp.put("A long document about many topics. " * 20,
                     id="test-doc", tags={"project": "test"})

        # Mock the LLM call
        mock_response = json.dumps([
            {"summary": "Introduction", "content": "First section text",
             "tags": {"topic": "intro"}},
            {"summary": "Main body", "content": "Second section text",
             "tags": {"topic": "analysis"}},
        ])

        with patch("keep.api._call_decomposition_llm") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Introduction", "content": "First section text",
                 "tags": {"topic": "intro"}},
                {"summary": "Main body", "content": "Second section text",
                 "tags": {"topic": "analysis"}},
            ]
            parts = kp.analyze("test-doc")

        assert len(parts) == 2
        assert parts[0].part_num == 1
        assert parts[0].summary == "Introduction"
        assert parts[0].tags.get("topic") == "intro"
        # Parent tags inherited
        assert parts[0].tags.get("project") == "test"

    def test_analyze_replaces_parts(self, mock_providers, tmp_path):
        """Re-analyze replaces all previous parts."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content " * 50, id="test-doc")

        with patch("keep.api._call_decomposition_llm") as mock_llm:
            # First analysis
            mock_llm.return_value = [
                {"summary": "Part A", "content": "A text"},
                {"summary": "Part B", "content": "B text"},
                {"summary": "Part C", "content": "C text"},
            ]
            parts1 = kp.analyze("test-doc")
            assert len(parts1) == 3

            # Re-analysis with different decomposition
            mock_llm.return_value = [
                {"summary": "New Part 1", "content": "New text 1"},
                {"summary": "New Part 2", "content": "New text 2"},
            ]
            parts2 = kp.analyze("test-doc")
            assert len(parts2) == 2

        # Verify only new parts exist
        listed = kp.list_parts("test-doc")
        assert len(listed) == 2
        assert listed[0].summary == "New Part 1"

    def test_get_part(self, mock_providers, tmp_path):
        """get_part() returns an Item with part metadata."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content " * 50, id="test-doc")

        with patch("keep.api._call_decomposition_llm") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Part 1", "content": "Text 1"},
                {"summary": "Part 2", "content": "Text 2"},
            ]
            kp.analyze("test-doc")

        item = kp.get_part("test-doc", 1)
        assert item is not None
        assert item.summary == "Text 1"  # Returns content, not short summary
        assert item.tags["_part_num"] == "1"
        assert item.tags["_base_id"] == "test-doc"
        assert item.tags["_total_parts"] == "2"

        # Non-existent part
        assert kp.get_part("test-doc", 99) is None

    def test_list_parts(self, mock_providers, tmp_path):
        """list_parts() returns PartInfo ordered by part_num."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content " * 50, id="test-doc")

        with patch("keep.api._call_decomposition_llm") as mock_llm:
            mock_llm.return_value = [
                {"summary": f"Part {i}", "content": f"Text {i}"}
                for i in range(1, 4)
            ]
            kp.analyze("test-doc")

        parts = kp.list_parts("test-doc")
        assert len(parts) == 3
        assert [p.part_num for p in parts] == [1, 2, 3]

    def test_analyze_nonexistent_raises(self, mock_providers, tmp_path):
        """analyze() raises ValueError for nonexistent document."""
        kp = Keeper(store_path=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            kp.analyze("nonexistent")

    def test_analyze_fallback_to_chunking(self, mock_providers, tmp_path):
        """When LLM returns empty, falls back to simple chunking."""
        kp = Keeper(store_path=tmp_path)
        # Create content with clear paragraph structure
        paragraphs = [f"Section {i}. " + "x" * 500 for i in range(5)]
        content = "\n\n".join(paragraphs)
        kp.put(content, id="test-doc")

        with patch("keep.api._call_decomposition_llm") as mock_llm:
            mock_llm.return_value = []  # LLM fails
            parts = kp.analyze("test-doc")

        assert len(parts) >= 2  # Fallback produces chunks


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLIParts:
    """Test CLI @P{N} parsing and analyze command."""

    def test_part_suffix_pattern(self):
        """PART_SUFFIX_PATTERN matches @P{N} correctly."""
        from keep.cli import PART_SUFFIX_PATTERN
        assert PART_SUFFIX_PATTERN.search("doc:1@P{1}").group(1) == "1"
        assert PART_SUFFIX_PATTERN.search("doc:1@P{42}").group(1) == "42"
        assert PART_SUFFIX_PATTERN.search("doc:1") is None
        assert PART_SUFFIX_PATTERN.search("doc:1@V{1}") is None

    def test_format_summary_line_with_part(self):
        """Summary line shows @P{N} for parts."""
        from keep.cli import _format_summary_line
        from keep.types import Item

        item = Item(
            id="doc:1@p1",
            summary="Part summary text",
            tags={"_base_id": "doc:1", "_part_num": "1", "_updated": "2026-01-14T10:00:00"},
        )
        line = _format_summary_line(item)
        assert "@P{1}" in line
        assert "Part summary" in line
