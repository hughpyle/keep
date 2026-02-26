"""
Tests for hybrid search (semantic + FTS5) and RRF fusion.
"""

import pytest

from keep.api import Keeper
from keep.types import Item


# ---------------------------------------------------------------------------
# RRF fusion unit tests
# ---------------------------------------------------------------------------


class TestRRFFuse:
    """Unit tests for Keeper._rrf_fuse static method."""

    def test_single_list_semantic_only(self):
        """Items only in semantic list get valid RRF scores."""
        sem = [Item(id="a", summary="x"), Item(id="b", summary="y")]
        fts = []
        result = Keeper._rrf_fuse(sem, fts, k=60)
        assert [r.id for r in result] == ["a", "b"]
        # Semantic weight=1, max=(1+2)/(61)=3/61, so 1/61 normalized = 1/3
        assert result[0].score == pytest.approx(1 / 3, abs=0.01)

    def test_single_list_fts_only(self):
        """FTS-only items score higher than semantic-only due to 2x weight."""
        sem = []
        fts = [Item(id="a", summary="x"), Item(id="b", summary="y")]
        result = Keeper._rrf_fuse(sem, fts, k=60)
        assert [r.id for r in result] == ["a", "b"]
        # FTS weight=2, so 2/61 normalized by 3/61 = 2/3
        assert result[0].score == pytest.approx(2 / 3, abs=0.01)

    def test_overlap_boosted(self):
        """Items in both lists score higher than items in one."""
        sem = [Item(id="a", summary="x"), Item(id="b", summary="y")]
        fts = [Item(id="a", summary="x"), Item(id="c", summary="z")]
        result = Keeper._rrf_fuse(sem, fts, k=60)
        # "a" appears in both, should be ranked first
        assert result[0].id == "a"
        assert result[0].score > result[1].score

    def test_rank_1_both_lists_is_1_0(self):
        """Item ranked #1 in both lists gets normalized score of 1.0."""
        sem = [Item(id="a", summary="x")]
        fts = [Item(id="a", summary="x")]
        result = Keeper._rrf_fuse(sem, fts, k=60)
        assert result[0].score == pytest.approx(1.0, abs=0.001)

    def test_empty_inputs(self):
        """Empty inputs produce empty output."""
        result = Keeper._rrf_fuse([], [], k=60)
        assert result == []

    def test_prefers_semantic_item(self):
        """When item appears in both lists, uses semantic Item (has tags)."""
        sem = [Item(id="a", summary="x", tags={"topic": "auth"})]
        fts = [Item(id="a", summary="x")]
        result = Keeper._rrf_fuse(sem, fts, k=60)
        assert result[0].tags == {"topic": "auth"}

    def test_ordering_by_best_rank(self):
        """Items ranked higher in one list beat items ranked medium in both."""
        sem = [Item(id="a", summary="1st"), Item(id="b", summary="2nd")]
        fts = [Item(id="b", summary="2nd"), Item(id="c", summary="3rd")]
        result = Keeper._rrf_fuse(sem, fts, k=60)
        # b is in both lists (rank 2+1), a is in one (rank 1), c in one (rank 2)
        # b should score highest
        assert result[0].id == "b"


# ---------------------------------------------------------------------------
# Integration tests: hybrid find through Keeper
# ---------------------------------------------------------------------------


class TestHybridFind:
    """Integration tests for hybrid search through Keeper.find()."""

    @pytest.fixture
    def kp(self, mock_providers, tmp_path):
        kp = Keeper(store_path=tmp_path)
        kp._get_embedding_provider()
        kp.put("Alice likes cats and dogs", id="alice:pets")
        kp.put("Bob works on quantum computing", id="bob:work")
        kp.put("The weather forecast for tomorrow", id="weather")
        return kp

    def test_find_returns_results(self, kp):
        """Basic find returns results with scores."""
        results = kp.find("cats")
        assert len(results) > 0
        assert all(r.score is not None for r in results)

    def test_find_keyword_match(self, kp):
        """FTS keyword matching helps surface exact term matches."""
        results = kp.find("quantum")
        ids = {r.id for r in results}
        assert "bob:work" in ids

    def test_find_scores_are_normalized(self, kp):
        """All hybrid scores should be in [0, 1]."""
        results = kp.find("cats")
        for r in results:
            assert 0 <= r.score <= 1.0


class TestFTSOnlyFallback:
    """Tests for FTS-only mode when no embedding provider is configured."""

    @pytest.fixture
    def kp_no_embed(self, mock_providers, tmp_path):
        """Keeper with no embedding provider configured."""
        kp = Keeper(store_path=tmp_path)
        kp._config.embedding = None
        # Put items with explicit embedding (bypasses provider check)
        kp._document_store.upsert("default", "doc:1", "Alice likes cats and dogs", {})
        kp._document_store.upsert("default", "doc:2", "Bob works on quantum computing", {})
        return kp

    def test_fts_only_returns_results(self, kp_no_embed):
        """find() works without embedding provider via FTS fallback."""
        results = kp_no_embed.find("cats")
        assert len(results) > 0

    def test_fts_only_no_scores(self, kp_no_embed):
        """FTS-only results have no similarity scores."""
        results = kp_no_embed.find("cats")
        for r in results:
            assert r.score is None
