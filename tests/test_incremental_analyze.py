"""Tests for incremental vstring analysis."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from keep.api import Keeper
from keep.document_store import VersionInfo


# Content long enough to pass the min_analyze_length floor (500 chars total
# across assembled version chunks).  Each version ~120 chars so 5 versions
# produce ~600+ chars of assembled content.
_V1 = "First version: project kickoff and initial requirements gathering phase with stakeholder interviews and scope definition"
_V2 = "Second version: architecture review and technology stack decisions made after evaluating multiple database and framework options"
_V3 = "Third version: implementation started with core module development including authentication, authorization, and data models"
_V4 = "Fourth version: testing infrastructure and CI/CD pipeline configured with integration tests covering all critical user paths"
_V5 = "Fifth version: performance optimization and load testing results showing significant improvements in query response latency"


def _version_summary(i: int) -> str:
    """Build a version summary long enough for assembled chunks to pass the analysis floor."""
    return (
        f"Version {i} summary: project milestone covering requirements, design, "
        f"implementation, testing, and deployment activities for iteration {i} "
        f"of the platform development cycle"
    )


def _make_versions(n, start=1):
    """Create a list of VersionInfo objects (newest-first)."""
    versions = []
    for i in range(n, start - 1, -1):
        versions.append(VersionInfo(
            version=i,
            summary=_version_summary(i),
            tags={},
            created_at=f"2026-03-{i:02d}T00:00:00",
            content_hash=f"hash_{i}",
        ))
    return versions


def _mock_parts(*summaries):
    """Build a list of part dicts from summaries."""
    return [{"summary": s, "content": ""} for s in summaries]


class TestIncrementalAnalyze:
    """Test incremental vstring analysis."""

    def _setup(self, kp, versions=3):
        """Put content with multiple versions."""
        for i in range(1, versions + 1):
            kp.put(_version_summary(i), id="doc1")

    def test_full_analysis_sets_analyzed_version(self, mock_providers, tmp_path):
        """Full analyze() records _analyzed_version tag."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=3)

        fake_versions = _make_versions(3)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=fake_versions):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            kp.analyze("doc1", force=True)

        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "doc1")
        assert doc.tags.get("_analyzed_version") == "3"

    def test_incremental_appends_parts(self, mock_providers, tmp_path):
        """Incremental analysis appends new parts without deleting old ones."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=3)

        doc_coll = kp._resolve_doc_collection()

        # First: full analysis with 3 versions → produces 2 parts
        versions_3 = _make_versions(3)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_3):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            parts = kp.analyze("doc1", force=True)

        assert len(parts) == 2

        # Add more versions
        kp.put(_V4, id="doc1")
        kp.put(_V5, id="doc1")

        versions_5 = _make_versions(5)
        with patch.object(kp._document_store, "list_versions", return_value=versions_5):
            # Mock the provider's generate method for incremental single-window call
            mock_provider = MagicMock()
            mock_provider._provider = None  # prevent CachingProvider unwrap
            mock_provider.generate.return_value = "New theme C emerged in the project direction\nNew decision D was made about architecture"
            with patch.object(kp, "_get_summarization_provider", return_value=mock_provider):
                parts = kp.analyze("doc1")

        part_nums = sorted(p.part_num for p in parts)
        # Should have old parts (1, 2) + new parts (3, 4)
        assert 1 in part_nums
        assert 2 in part_nums
        assert 3 in part_nums
        assert 4 in part_nums

        # Old parts should be preserved
        old_part_1 = next(p for p in parts if p.part_num == 1)
        assert old_part_1.summary == "Part A"

    def test_incremental_skips_when_no_new_versions(self, mock_providers, tmp_path):
        """When _analyzed_version matches highest version, skip without LLM call."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=3)

        doc_coll = kp._resolve_doc_collection()

        # Full analysis
        versions_3 = _make_versions(3)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_3):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            kp.analyze("doc1", force=True)

        # Incremental with same versions — should skip
        mock_provider = MagicMock()
        mock_provider._provider = None
        with patch.object(kp._document_store, "list_versions", return_value=versions_3), \
             patch.object(kp, "_get_summarization_provider", return_value=mock_provider):
            parts = kp.analyze("doc1")

        # generate() should NOT have been called
        mock_provider.generate.assert_not_called()

    def test_force_bypasses_incremental(self, mock_providers, tmp_path):
        """force=True always does full analysis."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=3)

        doc_coll = kp._resolve_doc_collection()

        # Full analysis to set _analyzed_version
        versions_3 = _make_versions(3)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_3):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            kp.analyze("doc1", force=True)

        # Add more versions
        kp.put(_V4, id="doc1")
        versions_4 = _make_versions(4)

        # force=True should go through full path (SlidingWindowAnalyzer.analyze)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_4):
            mock_llm.return_value = _mock_parts("Part X", "Part Y", "Part Z")
            parts = kp.analyze("doc1", force=True)

        # Should have replaced all parts
        assert len(parts) == 3
        assert parts[0].summary == "Part X"

    def test_incremental_updates_analyzed_version(self, mock_providers, tmp_path):
        """Incremental analysis updates _analyzed_version to highest version."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=3)

        doc_coll = kp._resolve_doc_collection()

        # Full analysis
        versions_3 = _make_versions(3)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_3):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            kp.analyze("doc1", force=True)

        doc = kp._document_store.get(doc_coll, "doc1")
        assert doc.tags.get("_analyzed_version") == "3"

        # Add versions and do incremental
        kp.put(_V4, id="doc1")
        kp.put(_V5, id="doc1")
        versions_5 = _make_versions(5)
        with patch.object(kp._document_store, "list_versions", return_value=versions_5):
            mock_provider = MagicMock()
            mock_provider._provider = None
            mock_provider.generate.return_value = "New development found"
            with patch.object(kp, "_get_summarization_provider", return_value=mock_provider):
                kp.analyze("doc1")

        doc = kp._document_store.get(doc_coll, "doc1")
        assert doc.tags.get("_analyzed_version") == "5"

    def test_incremental_context_chunks_provided(self, mock_providers, tmp_path):
        """Incremental analysis includes overlap context versions in the prompt."""
        kp = Keeper(store_path=tmp_path)
        self._setup(kp, versions=5)

        doc_coll = kp._resolve_doc_collection()

        # Full analysis at version 5
        versions_5 = _make_versions(5)
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=versions_5):
            mock_llm.return_value = _mock_parts("Part A", "Part B")
            kp.analyze("doc1", force=True)

        # Add 2 more versions
        kp.put("Version 6 content", id="doc1")
        kp.put("Version 7 content", id="doc1")
        versions_7 = _make_versions(7)

        # Do incremental
        with patch.object(kp._document_store, "list_versions", return_value=versions_7):
            mock_provider = MagicMock()
            mock_provider._provider = None
            mock_provider.generate.return_value = "New theme appeared"
            with patch.object(kp, "_get_summarization_provider", return_value=mock_provider):
                kp.analyze("doc1")

        # The generate call should have context (old versions) outside <analyze>
        # and targets (new versions) inside <analyze>
        call_args = mock_provider.generate.call_args
        if call_args:
            user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("prompt", "")
            assert "<analyze>" in user_prompt
            assert "</analyze>" in user_prompt
            assert "<content>" in user_prompt


class TestGatherAnalyzeChunksIncremental:
    """Test _gather_analyze_chunks with since_version."""

    def test_returns_dict_for_incremental(self, mock_providers, tmp_path):
        """With since_version set, returns dict with context and targets."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Some content here for testing purposes", id="doc1")

        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "doc1")

        versions = _make_versions(5)
        with patch.object(kp._document_store, "list_versions", return_value=versions):
            result = kp._gather_analyze_chunks("doc1", doc, since_version=3)

        assert isinstance(result, dict)
        assert "context" in result
        assert "targets" in result
        # Context: versions 1-3 (but capped by INCREMENTAL_CONTEXT)
        assert len(result["context"]) == 3
        # Targets: versions 4-5 + current
        assert len(result["targets"]) == 3

    def test_returns_empty_targets_when_no_new_versions(self, mock_providers, tmp_path):
        """When since_version >= max version, targets is empty."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content", id="doc1")

        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "doc1")

        versions = _make_versions(3)
        with patch.object(kp._document_store, "list_versions", return_value=versions):
            result = kp._gather_analyze_chunks("doc1", doc, since_version=3)

        assert isinstance(result, dict)
        assert result["targets"] == []

    def test_returns_flat_list_without_since_version(self, mock_providers, tmp_path):
        """Without since_version, returns flat list (full analysis)."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content", id="doc1")

        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "doc1")

        versions = _make_versions(3)
        with patch.object(kp._document_store, "list_versions", return_value=versions):
            result = kp._gather_analyze_chunks("doc1", doc)

        assert isinstance(result, list)

    def test_context_limited_to_incremental_context(self, mock_providers, tmp_path):
        """Only last INCREMENTAL_CONTEXT versions included as context."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Content", id="doc1")

        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, "doc1")

        versions = _make_versions(20)
        with patch.object(kp._document_store, "list_versions", return_value=versions):
            result = kp._gather_analyze_chunks("doc1", doc, since_version=15)

        assert isinstance(result, dict)
        # Context should be capped at INCREMENTAL_CONTEXT (10)
        assert len(result["context"]) == 10
        # Targets: versions 16-20 + current = 6
        assert len(result["targets"]) == 6

    def test_uri_note_with_custom_id_refetches_source_uri(self, mock_providers, tmp_path):
        """URI-backed notes with custom IDs should re-fetch via _source_uri."""
        kp = Keeper(store_path=tmp_path)
        doc_coll = kp._resolve_doc_collection()
        kp._document_store.upsert(
            doc_coll,
            "doc-custom",
            summary="truncated summary",
            tags={"_source": "uri", "_source_uri": "file:///tmp/original.md"},
            content_hash="hash1",
        )
        doc = kp._document_store.get(doc_coll, "doc-custom")

        with patch.object(kp._document_provider, "fetch") as mock_fetch:
            mock_fetch.return_value.content = "full source content"
            result = kp._gather_analyze_chunks("doc-custom", doc)

        mock_fetch.assert_called_once_with("file:///tmp/original.md")
        assert result == [{"content": "full source content", "tags": {}, "index": 0}]


class TestMaxPartNum:
    """Test DocumentStore.max_part_num."""

    def test_returns_zero_for_no_parts(self, mock_providers, tmp_path):
        kp = Keeper(store_path=tmp_path)
        kp.put("Content", id="doc1")
        doc_coll = kp._resolve_doc_collection()
        assert kp._document_store.max_part_num(doc_coll, "doc1") == 0

    def test_returns_highest_part_num(self, mock_providers, tmp_path):
        from keep.document_store import PartInfo
        kp = Keeper(store_path=tmp_path)
        kp.put("Content", id="doc1")
        doc_coll = kp._resolve_doc_collection()

        for i in [1, 2, 5]:
            part = PartInfo(
                part_num=i, summary=f"Part {i}", tags={},
                content="", created_at="2026-03-12T00:00:00",
            )
            kp._document_store.upsert_single_part(doc_coll, "doc1", part)

        assert kp._document_store.max_part_num(doc_coll, "doc1") == 5
