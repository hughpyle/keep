"""Tests for @P{0} vstring overview summary in analyze."""

from unittest.mock import patch

from keep.api import Keeper
from keep.document_store import VersionInfo

# Content long enough so the assembled vstring passes the 50-char minimum
_V1 = "Version one covers machine learning fundamentals and supervised algorithms"
_V2 = "Version two adds neural network architectures and deep learning updates"

# A fake version record to make _gather_analyze_chunks produce 2+ chunks
_FAKE_VERSION = VersionInfo(
    version=1,
    summary=_V1,
    tags={},
    created_at="2026-02-25T00:00:00",
)


def _mock_parts():
    return [
        {"summary": "ML basics", "content": "Machine learning intro"},
        {"summary": "Neural nets", "content": "Neural network details"},
    ]


def _setup(kp, doc_id="doc1", tags=None):
    """Put content and patch list_versions to simulate version history."""
    kp.put(_V1, id=doc_id, tags=tags or {})
    kp.put(_V2, id=doc_id)


class TestVstringOverview:
    """Test @P{0} overview generation during analyze()."""

    def _analyze_with_versions(self, kp, doc_id="doc1", parts=None):
        """Run analyze with mocked SlidingWindowAnalyzer and version history."""
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=[_FAKE_VERSION]):
            mock_llm.return_value = parts or _mock_parts()
            return kp.analyze(doc_id, force=True)

    def test_analyze_produces_overview_part(self, mock_providers, tmp_path):
        """analyze() creates @P{0} with vstring overview when item has versions."""
        kp = Keeper(store_path=tmp_path)
        _setup(kp)

        parts = self._analyze_with_versions(kp)

        assert any(p.part_num == 0 for p in parts)
        overview = next(p for p in parts if p.part_num == 0)
        assert len(overview.summary) > 0
        assert overview.tags.get("_part_type") == "overview"

    def test_analyze_no_overview_for_single_version(self, mock_providers, tmp_path):
        """Single-version items don't get @P{0} — only one chunk."""
        kp = Keeper(store_path=tmp_path)
        kp.put("Single version content that is long enough to analyze " * 5,
               id="doc2")

        # list_versions returns [] (default mock), so only 1 chunk
        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm:
            mock_llm.return_value = _mock_parts()
            parts = kp.analyze("doc2", force=True)

        assert not any(p.part_num == 0 for p in parts)

    def test_overview_part_is_searchable(self, mock_providers, tmp_path):
        """@P{0} overview gets its own embedding in ChromaDB."""
        kp = Keeper(store_path=tmp_path)
        _setup(kp, "doc3")

        self._analyze_with_versions(kp, "doc3")

        # @P{0} should exist in ChromaDB as doc3@p0
        chroma_coll = kp._resolve_chroma_collection()
        result = kp._store.get(chroma_coll, "doc3@p0")
        assert result is not None

    def test_overview_shows_first_in_list_parts(self, mock_providers, tmp_path):
        """@P{0} appears first in list_parts() — before @P{1}, @P{2}."""
        kp = Keeper(store_path=tmp_path)
        _setup(kp, "doc4")

        parts = self._analyze_with_versions(kp, "doc4")

        assert len(parts) >= 3  # @P{0} + @P{1} + @P{2}
        assert parts[0].part_num == 0
        assert parts[1].part_num == 1

    def test_overview_inherits_user_tags(self, mock_providers, tmp_path):
        """@P{0} inherits parent's user tags but not system tags."""
        kp = Keeper(store_path=tmp_path)
        _setup(kp, "doc5", tags={"project": "test", "topic": "ai"})

        parts = self._analyze_with_versions(kp, "doc5")

        overview = next(p for p in parts if p.part_num == 0)
        assert overview.tags.get("project") == "test"
        assert overview.tags.get("_part_type") == "overview"

    def test_reanalyze_replaces_overview(self, mock_providers, tmp_path):
        """Re-analysis produces a fresh @P{0}, not a duplicate."""
        kp = Keeper(store_path=tmp_path)
        _setup(kp, "doc6")

        with patch("keep.analyzers.SlidingWindowAnalyzer.analyze") as mock_llm, \
             patch.object(kp._document_store, "list_versions", return_value=[_FAKE_VERSION]):
            # First analysis
            mock_llm.return_value = [
                {"summary": "Old part A", "content": "Old A"},
                {"summary": "Old part B", "content": "Old B"},
            ]
            kp.analyze("doc6", force=True)

            # Re-analyze
            mock_llm.return_value = [
                {"summary": "New part X", "content": "New X"},
                {"summary": "New part Y", "content": "New Y"},
            ]
            parts = kp.analyze("doc6", force=True)

        # Should have exactly one @P{0}
        overviews = [p for p in parts if p.part_num == 0]
        assert len(overviews) == 1
        # Total: @P{0} + @P{1} + @P{2}
        assert len(parts) == 3
