"""Tests for document parts (structural decomposition).

Tests cover:
- DocumentStore CRUD for parts
- Schema migration (version 3→4)
- ChromaStore part methods
- Keeper.analyze() with mocked LLM
- Keeper.get_part() and list_parts()
- CLI @P{N} parsing
- Parts manifest in get output
- JSON decomposition parsing
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from keep.api import Keeper
from keep.analyzers import _parse_decomposition_json
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
            PartInfo(
                part_num=1,
                summary="Intro",
                tags={"topic": "overview"},
                created_at=now,
            ),
            PartInfo(
                part_num=2,
                summary="Main body",
                tags={"topic": "detail"},
                created_at=now,
            ),
        ]
        count = store.upsert_parts("default", "doc:1", parts)
        assert count == 2

        result = store.list_parts("default", "doc:1")
        assert len(result) == 2
        assert result[0].part_num == 1
        assert result[0].summary == "Intro"
        assert result[0].tags == {"topic": "overview"}
        assert result[1].part_num == 2

    def test_get_part(self, store):
        """Individual parts can be retrieved by number."""
        now = utc_now()
        parts = [
            PartInfo(part_num=1, summary="Part 1", tags={}, created_at=now),
            PartInfo(part_num=2, summary="Part 2", tags={}, created_at=now),
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
        parts = [PartInfo(i, f"Part {i}", {}, now) for i in range(1, 4)]
        store.upsert_parts("default", "doc:1", parts)
        assert store.part_count("default", "doc:1") == 3

    def test_delete_parts(self, store):
        """Parts can be deleted."""
        now = utc_now()
        parts = [PartInfo(1, "Part 1", {}, now)]
        store.upsert_parts("default", "doc:1", parts)
        assert store.part_count("default", "doc:1") == 1

        deleted = store.delete_parts("default", "doc:1")
        assert deleted == 1
        assert store.part_count("default", "doc:1") == 0

    def test_delete_single_part(self, store):
        """A single part can be deleted without touching siblings."""
        now = utc_now()
        parts = [
            PartInfo(1, "Part 1", {}, now),
            PartInfo(2, "Part 2", {}, now),
        ]
        store.upsert_parts("default", "doc:1", parts)

        deleted = store.delete_part("default", "doc:1", 1)
        assert deleted == 1
        assert store.get_part("default", "doc:1", 1) is None
        remaining = store.get_part("default", "doc:1", 2)
        assert remaining is not None
        assert remaining.summary == "Part 2"

    def test_upsert_replaces_atomically(self, store):
        """Re-upsert replaces all parts atomically."""
        now = utc_now()

        # Initial parts
        parts_v1 = [
            PartInfo(1, "Old part 1", {}, now),
            PartInfo(2, "Old part 2", {}, now),
            PartInfo(3, "Old part 3", {}, now),
        ]
        store.upsert_parts("default", "doc:1", parts_v1)
        assert store.part_count("default", "doc:1") == 3

        # Replace with fewer parts
        parts_v2 = [
            PartInfo(1, "New part 1", {"topic": "new"}, now),
            PartInfo(2, "New part 2", {}, now),
        ]
        store.upsert_parts("default", "doc:1", parts_v2)
        assert store.part_count("default", "doc:1") == 2

        result = store.list_parts("default", "doc:1")
        assert result[0].summary == "New part 1"
        assert result[0].tags == {"topic": "new"}

    def test_parts_isolated_by_id(self, store):
        """Parts for different documents are independent."""
        now = utc_now()
        store.upsert_parts("default", "doc:1", [PartInfo(1, "A", {}, now)])
        store.upsert_parts("default", "doc:2", [PartInfo(1, "B", {}, now)])

        assert store.part_count("default", "doc:1") == 1
        assert store.part_count("default", "doc:2") == 1

        store.delete_parts("default", "doc:1")
        assert store.part_count("default", "doc:1") == 0
        assert store.part_count("default", "doc:2") == 1

    def test_upsert_parts_deduplicates_tag_values(self, store):
        """upsert_parts() deduplicates multivalue tags per key."""
        now = utc_now()
        store.upsert_parts(
            "default",
            "doc:1",
            [PartInfo(1, "Part", {"k": ["a", "a", "b"]}, now)],
        )

        part = store.get_part("default", "doc:1", 1)
        assert part is not None
        assert part.tags == {"k": ["a", "b"]}

    def test_upsert_single_part_deduplicates_tag_values(self, store):
        """upsert_single_part() deduplicates multivalue tags per key."""
        now = utc_now()
        store.upsert_single_part(
            "default",
            "doc:1",
            PartInfo(1, "Part", {"k": ["a", "a", "b"]}, now),
        )

        part = store.get_part("default", "doc:1", 1)
        assert part is not None
        assert part.tags == {"k": ["a", "b"]}

    def test_update_part_tags_deduplicates_tag_values(self, store):
        """update_part_tags() deduplicates multivalue tags per key."""
        now = utc_now()
        store.upsert_parts(
            "default",
            "doc:1",
            [PartInfo(1, "Part", {"k": "a"}, now)],
        )

        updated = store.update_part_tags("default", "doc:1", 1, {"k": ["a", "a", "b"]})
        assert updated is True

        part = store.get_part("default", "doc:1", 1)
        assert part is not None
        assert part.tags == {"k": ["a", "b"]}


# ---------------------------------------------------------------------------
# Schema migration test
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    """Test that the current schema supports part storage."""

    def test_migration_creates_parts_table(self, tmp_path):
        """New databases get the document_parts table."""
        db_path = tmp_path / "test.db"
        store = DocumentStore(db_path)

        # Check table exists by inserting a part
        now = utc_now()
        parts = [PartInfo(1, "Test", {}, now)]
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
            {"summary": "Intro"},
            {"summary": "Body", "tags": {"topic": "main"}},
        ])
        result = _parse_decomposition_json(text)
        assert len(result) == 2
        assert result[0]["summary"] == "Intro"
        assert result[1]["tags"] == {"topic": "main"}

    def test_parse_code_fenced(self):
        text = '```json\n[{"summary": "Test"}]\n```'
        result = _parse_decomposition_json(text)
        assert len(result) == 1
        assert result[0]["summary"] == "Test"

    def test_parse_wrapper_object(self):
        text = json.dumps({"sections": [
            {"summary": "Part 1"},
        ]})
        result = _parse_decomposition_json(text)
        assert len(result) == 1
        assert result[0]["summary"] == "Part 1"

    def test_parse_empty_text(self):
        assert _parse_decomposition_json("") == []
        assert _parse_decomposition_json(None) == []

    def test_parse_invalid_json(self):
        assert _parse_decomposition_json("not json at all") == []

    def test_parse_skips_empty_entries(self):
        text = json.dumps([
            {"summary": "Good"},
            {},  # No summary
            {"summary": ""},  # Empty string
        ])
        result = _parse_decomposition_json(text)
        assert len(result) == 1


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

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Introduction", "tags": {"topic": "intro"}},
                {"summary": "Main body", "tags": {"topic": "analysis"}},
            ]
            parts = kp.analyze("test-doc", force=True)

        assert len(parts) == 2
        assert parts[0].part_num == 1
        assert parts[0].summary == "Introduction"
        # Analyzer-assigned tag is preserved.
        assert parts[0].tags.get("topic") == "intro"
        # Parent tags are NOT inherited — parts only carry analyzer-assigned
        # tags plus _base_id/_part_num bookkeeping. See analyze.py for the
        # rationale (no drift, no edge-tag clones).
        assert "project" not in parts[0].tags
        assert parts[0].tags.get("_base_id") == "test-doc"
        assert parts[0].tags.get("_part_num") == "1"

    def test_parts_do_not_inherit_edge_tags_from_parent(self, mock_providers, tmp_path):
        """Edge tags on the parent must not clone onto every part.

        This is the regression test for the in-the-wild bug where a
        survey paper's `informs`/`referenced_by` lists ended up cloned
        onto every analyzed part, turning each fragment into a noisy
        wearer of the parent's full citation graph.
        """
        from keep.types import utc_now
        kp = Keeper(store_path=tmp_path)

        # Create an edge tag (like `references`).
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()
        kp._document_store.upsert(
            collection=doc_coll,
            id=".tag/cites",
            summary="Tag: cites",
            tags={
                "_inverse": "cited_by", "_created": now, "_updated": now,
                "_source": "inline", "category": "system",
            },
        )

        kp.put(
            "A long document about many topics. " * 20,
            id="paper-A",
            tags={
                "topic": "graphs",  # content tag
                "year": "2024",     # content tag
                "cites": "https://example.com/other-paper[[Other Paper]]",  # edge tag
            },
        )

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Section 1"},
                {"summary": "Section 2"},
            ]
            parts = kp.analyze("paper-A", force=True)

        for part in parts:
            # Edge tag must not appear on parts.
            assert "cites" not in part.tags
            # Content tags from the parent must not appear either.
            assert "topic" not in part.tags
            assert "year" not in part.tags
            # System bookkeeping is present.
            assert part.tags.get("_base_id") == "paper-A"
            assert part.tags.get("_part_num") in ("1", "2")

    def test_analyze_replaces_parts(self, mock_providers, tmp_path):
        """Re-analyze replaces all previous parts."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content for analysis testing with enough length. " * 12, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            # First analysis (force to bypass single-version-untruncated skip)
            mock_llm.return_value = [
                {"summary": "Part A"},
                {"summary": "Part B"},
                {"summary": "Part C"},
            ]
            parts1 = kp.analyze("test-doc", force=True)
            assert len(parts1) == 3

            # Re-analysis with different decomposition (force=True since content unchanged)
            mock_llm.return_value = [
                {"summary": "New Part 1"},
                {"summary": "New Part 2"},
            ]
            parts2 = kp.analyze("test-doc", force=True)
            assert len(parts2) == 2

        # Verify only new parts exist
        listed = kp.list_parts("test-doc")
        assert len(listed) == 2
        assert listed[0].summary == "New Part 1"

    def test_get_part(self, mock_providers, tmp_path):
        """get_part() returns an Item with part metadata."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content for analysis testing with enough length. " * 12, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Text 1"},
                {"summary": "Text 2"},
            ]
            kp.analyze("test-doc", force=True)

        item = kp.get_part("test-doc", 1)
        assert item is not None
        assert item.summary == "Text 1"
        assert item.tags["_part_num"] == "1"
        assert item.tags["_base_id"] == "test-doc"
        assert item.tags["_total_parts"] == "2"

        # Non-existent part
        assert kp.get_part("test-doc", 99) is None

    def test_list_parts(self, mock_providers, tmp_path):
        """list_parts() returns PartInfo ordered by part_num."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content for analysis testing with enough length. " * 12, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": f"Text {i}"}
                for i in range(1, 4)
            ]
            kp.analyze("test-doc", force=True)

        parts = kp.list_parts("test-doc")
        assert len(parts) == 3
        assert [p.part_num for p in parts] == [1, 2, 3]

    def test_startup_removes_legacy_overview_part(self, mock_providers, tmp_path):
        """Keeper startup deletes legacy @P{0} rows once and persists that cleanup."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content for analysis testing with enough length. " * 12, id="test-doc")
        doc_coll = kp._resolve_doc_collection()
        chroma_coll = kp._resolve_chroma_collection()
        now = utc_now()
        kp._document_store.upsert_single_part(
            doc_coll,
            "test-doc",
            PartInfo(0, "Legacy overview", {}, now),
        )
        embedding = kp._get_embedding_provider().embed("Legacy overview")
        kp._store.upsert_part(
            chroma_coll,
            "test-doc",
            0,
            embedding,
            "Legacy overview",
            {},
        )
        assert kp._document_store.get_part(doc_coll, "test-doc", 0) is not None
        assert kp._store.get(chroma_coll, "test-doc@p0") is not None
        kp.close()

        kp2 = Keeper(store_path=tmp_path)
        doc_coll2 = kp2._resolve_doc_collection()
        chroma_coll2 = kp2._resolve_chroma_collection()
        assert kp2._document_store.get_part(doc_coll2, "test-doc", 0) is None
        assert kp2._store.get(chroma_coll2, "test-doc@p0") is None
        assert kp2._config.legacy_overview_parts_cleaned is True
        kp2.close()

        with patch.object(Keeper, "_cleanup_legacy_overview_parts", autospec=True) as cleanup:
            kp3 = Keeper(store_path=tmp_path)
            try:
                cleanup.assert_not_called()
            finally:
                kp3.close()

    def test_analyze_nonexistent_raises(self, mock_providers, tmp_path):
        """analyze() raises ValueError for nonexistent document."""
        kp = Keeper(store_path=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            kp.analyze("nonexistent")


# ---------------------------------------------------------------------------
# analyze() skip / _analyzed_hash tests
# ---------------------------------------------------------------------------


class TestAnalyzeSkip:
    """Test _analyzed_hash skip logic in analyze() and enqueue_analyze()."""

    MOCK_PARTS = [
        {"summary": "Text A", "tags": {"topic": "a"}},
        {"summary": "Text B", "tags": {"topic": "b"}},
    ]

    def test_analyze_sets_analyzed_hash(self, mock_providers, tmp_path):
        """analyze() sets _analyzed_hash tag on the document after success."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

        item = kp.get("test-doc")
        assert "_analyzed_hash" in item.tags
        # Hash should match the document's content_hash
        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "test-doc")
        assert item.tags["_analyzed_hash"] == doc.content_hash

    def test_analyze_skips_when_current(self, mock_providers, tmp_path):
        """analyze() skips LLM call when parts are already current."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            parts1 = kp.analyze("test-doc", force=True)
            assert len(parts1) == 2

            # Second call should skip (returns existing parts, no LLM call)
            mock_llm.reset_mock()
            parts2 = kp.analyze("test-doc")
            assert len(parts2) == 2
            mock_llm.assert_not_called()

    def test_analyze_reruns_after_content_change(self, mock_providers, tmp_path):
        """analyze() re-runs when content changes after previous analysis."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Original content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

            # Change content
            kp.put("Completely different content. " * 20, id="test-doc")

            # Should re-analyze (content_hash changed)
            mock_llm.reset_mock()
            mock_llm.return_value = [
                {"summary": "New text A"},
                {"summary": "New text B"},
            ]
            parts = kp.analyze("test-doc", force=True)
            mock_llm.assert_called_once()
            assert parts[0].summary == "New text A"

    def test_analyze_force_overrides_skip(self, mock_providers, tmp_path):
        """analyze(force=True) re-analyzes even when parts are current."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

            # Force re-analysis
            mock_llm.reset_mock()
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)
            mock_llm.assert_called_once()

    def test_enqueue_skips_when_current(self, mock_providers, tmp_path):
        """enqueue_analyze() returns False when parts are current."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc")

        # Enqueue should return False (already analyzed)
        result = kp.enqueue_analyze("test-doc")
        assert result is False

    def test_enqueue_accepts_when_stale(self, mock_providers, tmp_path):
        """enqueue_analyze() returns True when content changed."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc")

        # Change content
        kp.put("Different content entirely. " * 20, id="test-doc")

        # Enqueue should return True (needs re-analysis)
        result = kp.enqueue_analyze("test-doc")
        assert result is True

    def test_enqueue_force_overrides(self, mock_providers, tmp_path):
        """enqueue_analyze(force=True) enqueues even when current."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc")

        # Force enqueue
        result = kp.enqueue_analyze("test-doc", force=True)
        assert result is True

    def test_enqueue_accepts_never_analyzed(self, mock_providers, tmp_path):
        """enqueue_analyze() returns True for never-analyzed documents."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Long content for analysis. " * 20, id="test-doc")

        result = kp.enqueue_analyze("test-doc")
        assert result is True

    def test_analyze_skips_single_version_untruncated(self, mock_providers, tmp_path):
        """analyze() skips when content is untruncated and has no version thread."""
        kp = Keeper(store_path=tmp_path)
        # Content > min_analyze_length (500) but < max_summary_length (2000)
        # Mock store has no versions → single chunk → skip applies
        kp.put("A note about an interesting topic with details. " * 12, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Text A"},
            ]
            parts = kp.analyze("test-doc")
            mock_llm.assert_not_called()  # LLM should never be invoked

        # No parts created — skip was applied
        listed = kp.list_parts("test-doc")
        assert len(listed) == 0

        # _analyzed_hash should be recorded to prevent re-enqueue
        item = kp.get("test-doc")
        assert "_analyzed_hash" in item.tags

    def test_analyze_force_bypasses_single_version_skip(self, mock_providers, tmp_path):
        """analyze(force=True) runs even on single-version untruncated content."""
        kp = Keeper(store_path=tmp_path)
        # Same content as skip test above, but force=True bypasses the guard
        kp.put("A note about an interesting topic with details. " * 12, id="test-doc")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = [
                {"summary": "Text A"},
            ]
            parts = kp.analyze("test-doc", force=True)
            mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# Part-to-parent uplift in find()
# ---------------------------------------------------------------------------


class TestFindPartUplift:
    """find() replaces part hits with their parent document."""

    MOCK_PARTS = [
        {"summary": "Went to Miami in January",
         "tags": {"topic": "travel"}},
        {"summary": "Looking at flights to NYC",
         "tags": {"topic": "travel"}},
        {"summary": "Finished the project report",
         "tags": {"topic": "work"}},
    ]

    def test_find_uplifts_part_to_parent(self, mock_providers, tmp_path):
        """When find() hits a part, it returns the parent with _focus_part."""
        kp = Keeper(store_path=tmp_path)
        kp.put("A multi-topic document about trips and work. " * 20,
               id="test-doc", tags={"project": "journal"})

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

        # Search for something a part matches
        results = kp.find("Miami trip")

        # Should find the parent, not the part
        parent_results = [r for r in results if r.id == "test-doc"]
        assert len(parent_results) >= 1
        parent = parent_results[0]
        # Should have _focus_part set
        assert "_focus_part" in parent.tags

        # Should NOT have raw part IDs in results
        part_results = [r for r in results if "@p" in r.id or "@P" in r.id]
        assert len(part_results) == 0

    def test_find_with_tag_filter_still_reaches_parts(self, mock_providers, tmp_path):
        """Tag-filtered find() must still reach parts via the _base_id join.

        Parts intentionally don't inherit parent tags (see analyze.py),
        so Chroma's marker-based where clause on its own would filter
        parts out of tag-filtered searches. The _base_id expansion in
        _find_direct lets parts of matching parents through.

        Without the expansion, this test would fail: the part carries
        no `project` tag of its own, so the Chroma tag filter would
        reject it before the part-uplift code ever runs.
        """
        kp = Keeper(store_path=tmp_path)
        kp.put(
            "A multi-topic document about trips and work. " * 20,
            id="test-doc",
            tags={"project": "journal"},
        )

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

        # Sanity check: parts do not carry the parent's project tag.
        part = kp.get_part("test-doc", 1)
        assert part is not None
        assert "project" not in part.tags

        # Query text hits a part summary ("Miami" is in the first part),
        # and the tag filter matches the parent. The part must still
        # reach the result set so it can be uplifted to the parent.
        results = kp.find("Miami trip", tags={"project": "journal"})
        parent_ids = {r.id for r in results}
        assert "test-doc" in parent_ids

    def test_find_with_tag_filter_reaches_parts_fts_only(self, mock_providers, tmp_path):
        """FTS-only find() (no embedding provider) must also reach parts via _base_id.

        Regression for the reviewer's finding: the first pass only
        patched the Chroma semantic where clause, leaving query_fts's
        p.tags_json filter narrower than the parent set.
        """
        kp = Keeper(store_path=tmp_path)
        kp.put(
            "A multi-topic document about trips and work. " * 20,
            id="test-doc",
            tags={"project": "journal"},
        )

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

        # Force the FTS-only branch by disabling the embedding provider.
        kp._config.embedding = None

        # The part contains "Miami" but carries no `project` tag. The
        # parent carries project=journal. The FTS part-tag filter must
        # allow the part through via the _base_id join.
        results = kp.find("Miami trip", tags={"project": "journal"})
        parent_ids = {r.id for r in results}
        assert "test-doc" in parent_ids

    def test_find_dedupes_multiple_part_hits(self, mock_providers, tmp_path):
        """Multiple parts of the same parent produce one result."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Travel travel travel around the world visiting many destinations. " * 10,
               id="test-doc", tags={"project": "journal"})

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = list(self.MOCK_PARTS)
            kp.analyze("test-doc", force=True)

        # "travel" matches multiple parts — should still get one parent
        results = kp.find("travel")
        parent_count = sum(1 for r in results if r.id == "test-doc")
        assert parent_count <= 1


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLIParts:
    """Test CLI @P{N} parsing and analyze command."""

    def test_format_summary_line_with_part(self):
        """Summary line shows @P{N} for parts."""
        from keep.console_support import _format_summary_line
        from keep.types import Item

        item = Item(
            id="doc:1@p1",
            summary="Part summary text",
            tags={"_base_id": "doc:1", "_part_num": "1", "_updated": "2026-01-14T10:00:00"},
        )
        line = _format_summary_line(item)
        assert "@P{1}" in line
        assert "Part summary" in line


# ---------------------------------------------------------------------------
# File stat fast-path tests
# ---------------------------------------------------------------------------


class TestFileStatFastPath:
    """Test stat-based fast path for file:// URI puts."""

    def test_stores_file_stat_tags(self, mock_providers, tmp_path):
        """put(uri=file://) stores _file_mtime_ns and _file_size tags."""
        kp = Keeper(store_path=tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, test content for stat tags.")
        file_uri = f"file://{test_file}"

        item = kp.put(uri=file_uri)
        assert item.changed is True
        assert "_file_mtime_ns" in item.tags
        assert "_file_size" in item.tags
        assert item.tags["_file_size"] == str(test_file.stat().st_size)

    def test_skips_read_when_stat_unchanged(self, mock_providers, tmp_path):
        """put() skips file read when stat (mtime+size) is unchanged."""
        kp = Keeper(store_path=tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, test content for stat fast path.")
        file_uri = f"file://{test_file}"

        kp.put(uri=file_uri)

        # Second put — stat fast path should skip the file read
        with patch.object(
            kp._document_provider, "fetch",
            wraps=kp._document_provider.fetch,
        ) as mock_fetch:
            item2 = kp.put(uri=file_uri)
            assert item2.changed is False
            mock_fetch.assert_not_called()

    def test_reads_file_when_stat_changes(self, mock_providers, tmp_path):
        """put() calls fetch() when file stat changes (fast path not used)."""
        import time
        kp = Keeper(store_path=tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Original content.")
        file_uri = f"file://{test_file}"

        kp.put(uri=file_uri)

        # Modify file (sleep to ensure mtime_ns changes)
        time.sleep(0.01)
        test_file.write_text("Modified content, now different!")

        # Verify fetch IS called (stat changed → fast path falls through)
        with patch.object(
            kp._document_provider, "fetch",
            wraps=kp._document_provider.fetch,
        ) as mock_fetch:
            kp.put(uri=file_uri)
            mock_fetch.assert_called_once()

    def test_falls_through_when_tags_differ(self, mock_providers, tmp_path):
        """put() reads file when user tags differ, even if stat unchanged."""
        kp = Keeper(store_path=tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Stable content.")
        file_uri = f"file://{test_file}"

        kp.put(uri=file_uri, tags={"project": "alpha"})

        # Same file, different tags — must not use fast path
        with patch.object(
            kp._document_provider, "fetch",
            wraps=kp._document_provider.fetch,
        ) as mock_fetch:
            kp.put(uri=file_uri, tags={"project": "beta"})
            mock_fetch.assert_called_once()

    def test_fast_path_with_same_tags(self, mock_providers, tmp_path):
        """put() uses fast path when user tags are the same as stored."""
        kp = Keeper(store_path=tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content with consistent tags.")
        file_uri = f"file://{test_file}"

        kp.put(uri=file_uri, tags={"project": "myproject"})

        # Same file, same tags — should use fast path
        with patch.object(
            kp._document_provider, "fetch",
            wraps=kp._document_provider.fetch,
        ) as mock_fetch:
            item2 = kp.put(uri=file_uri, tags={"project": "myproject"})
            assert item2.changed is False
            mock_fetch.assert_not_called()
