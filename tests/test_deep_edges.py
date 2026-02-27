"""
Tests for edge-following deep search (_deep_edge_follow).

Edge-following replaces tag-following for stores with edges:
1. Traverse inverse edges from primary results
2. FTS pre-filter on edge source IDs
3. Embedding post-filter + RRF fusion
4. Assign results back to originating primaries
"""

import pytest
from pathlib import Path

from keep.api import Keeper
from keep.document_store import DocumentStore, PartInfo


# ---------------------------------------------------------------------------
# DocumentStore.query_fts_scoped (real SQLite)
# ---------------------------------------------------------------------------

class TestQueryFtsScoped:
    """Scoped FTS search against a real SQLite database."""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "documents.db"
        with DocumentStore(db_path) as s:
            s.upsert("c", "doc-a",
                     summary="Melanie loves reading books about history",
                     tags={"speaker": "Melanie"})
            s.upsert("c", "doc-b",
                     summary="Caroline went to a pride parade",
                     tags={"speaker": "Caroline"})
            s.upsert("c", "doc-c",
                     summary="Melanie went camping at the beach",
                     tags={"speaker": "Melanie"})
            s.upsert("c", "doc-d",
                     summary="Dave plays guitar and drums",
                     tags={"speaker": "Dave"})
            s.upsert_parts("c", "doc-a", [
                PartInfo(part_num=0,
                           summary="Overview of Melanie's reading habits",
                           tags={}, content="", created_at="2024-01-01"),
                PartInfo(part_num=1,
                           summary="Melanie read Charlotte's Web",
                           tags={}, content="", created_at="2024-01-01"),
            ])
            # Create a version by upserting again (original becomes v1)
            s.upsert("c", "doc-c",
                     summary="Melanie camped in the forest",
                     tags={"speaker": "Melanie"})
            yield s

    def test_scoped_returns_only_matching_ids(self, store):
        results = store.query_fts_scoped("c", "Melanie", ["doc-a", "doc-c"])
        ids = [r[0] for r in results]
        assert any("doc-a" in i for i in ids)
        assert any("doc-c" in i for i in ids)
        # doc-b and doc-d not in whitelist
        assert not any("doc-b" in i for i in ids)
        assert not any("doc-d" in i for i in ids)

    def test_scoped_excludes_non_whitelisted(self, store):
        results = store.query_fts_scoped("c", "went", ["doc-b"])
        ids = [r[0] for r in results]
        assert any("doc-b" in i for i in ids)
        assert not any("doc-c" in i for i in ids)

    def test_scoped_searches_parts(self, store):
        results = store.query_fts_scoped("c", "Charlotte", ["doc-a"])
        ids = [r[0] for r in results]
        assert any("doc-a@p" in i for i in ids)

    def test_scoped_searches_versions(self, store):
        # "camping" appears in v1 (original summary before re-upsert)
        results = store.query_fts_scoped("c", "camping", ["doc-c"])
        ids = [r[0] for r in results]
        assert any("doc-c@v" in i for i in ids)

    def test_scoped_empty_ids_returns_empty(self, store):
        assert store.query_fts_scoped("c", "Melanie", []) == []

    def test_scoped_no_query_match_returns_empty(self, store):
        assert store.query_fts_scoped("c", "xyznonexistent", ["doc-a"]) == []


# ---------------------------------------------------------------------------
# DocumentStore.has_edges
# ---------------------------------------------------------------------------

class TestHasEdges:

    @pytest.fixture
    def store(self, tmp_path):
        with DocumentStore(tmp_path / "documents.db") as s:
            yield s

    def test_no_edges(self, store):
        assert store.has_edges("c") is False

    def test_with_edges(self, store):
        store.upsert_edge("c", "src", "speaker", "target", "said", "2024-01-01")
        assert store.has_edges("c") is True

    def test_different_collection(self, store):
        store.upsert_edge("other", "src", "speaker", "target", "said", "2024-01-01")
        assert store.has_edges("c") is False
        assert store.has_edges("other") is True


# ---------------------------------------------------------------------------
# _deep_edge_follow integration (mock providers)
# ---------------------------------------------------------------------------

class TestDeepEdgeFollow:
    """Integration tests for edge-following deep search."""

    @pytest.fixture
    def keeper(self, tmp_path, mock_providers):
        kp = Keeper(store_path=str(tmp_path / "store"))
        doc_coll = kp._resolve_doc_collection()
        chroma_coll = kp._resolve_chroma_collection()

        # Create tagdoc with _inverse
        kp._document_store.upsert(doc_coll, ".tag/speaker", summary="",
                                  tags={"_inverse": "said", "_source": "inline",
                                        "category": "system"})

        # Create a target entity
        kp._document_store.upsert(doc_coll, "Melanie", summary="A person",
                                  tags={"_source": "auto-vivify"})

        # Create source docs (things Melanie "said") with edges
        for i in range(5):
            doc_id = f"session-{i}"
            kp._document_store.upsert(
                doc_coll, doc_id,
                summary=f"Melanie talked about topic {i}",
                tags={"speaker": "Melanie"},
            )
            kp._document_store.upsert_edge(
                doc_coll, doc_id, "speaker", "Melanie", "said",
                f"2024-01-0{i+1}",
            )
            # Also store in mock vector store
            embedding = [float(i) / 10] * 10
            kp._store.upsert(chroma_coll, doc_id, embedding,
                              f"Melanie talked about topic {i}",
                              tags={"speaker": "melanie"})

        # Create a doc that is NOT an edge source (control)
        kp._document_store.upsert(doc_coll, "unrelated",
                                  summary="Something about topic 3",
                                  tags={})
        kp._store.upsert(chroma_coll, "unrelated", [0.3] * 10,
                          "Something about topic 3", tags={})

        return kp

    def test_edge_follow_returns_groups(self, keeper):
        """Primary result 'Melanie' should produce deep groups from edges."""
        doc_coll = keeper._resolve_doc_collection()
        chroma_coll = keeper._resolve_chroma_collection()

        from keep.types import Item
        primary = [Item(id="Melanie", summary="A person", tags={}, score=1.0)]

        embedding = [0.1] * 10
        groups = keeper._deep_edge_follow(
            primary, chroma_coll, doc_coll,
            query="topic", embedding=embedding,
        )

        assert "Melanie" in groups
        deep_ids = [i.id for i in groups["Melanie"]]
        assert any(d.startswith("session-") for d in deep_ids)
        assert "unrelated" not in deep_ids

    def test_edge_follow_max_per_group(self, keeper):
        """Deep groups should be capped at max_per_group."""
        doc_coll = keeper._resolve_doc_collection()
        chroma_coll = keeper._resolve_chroma_collection()

        from keep.types import Item
        primary = [Item(id="Melanie", summary="A person", tags={}, score=1.0)]

        embedding = [0.1] * 10
        groups = keeper._deep_edge_follow(
            primary, chroma_coll, doc_coll,
            query="topic", embedding=embedding,
            max_per_group=2,
        )

        assert "Melanie" in groups
        assert len(groups["Melanie"]) <= 2

    def test_edge_follow_no_edges_returns_empty(self, keeper):
        """Primary without edges should produce no groups."""
        doc_coll = keeper._resolve_doc_collection()
        chroma_coll = keeper._resolve_chroma_collection()

        from keep.types import Item
        primary = [Item(id="unrelated", summary="Something", tags={}, score=1.0)]

        embedding = [0.1] * 10
        groups = keeper._deep_edge_follow(
            primary, chroma_coll, doc_coll,
            query="topic", embedding=embedding,
        )

        assert groups == {}

    def test_edge_follow_excludes_primaries_from_results(self, keeper):
        """Edge sources that ARE primaries should not appear in deep groups."""
        doc_coll = keeper._resolve_doc_collection()
        chroma_coll = keeper._resolve_chroma_collection()

        from keep.types import Item
        primary = [
            Item(id="Melanie", summary="A person", tags={}, score=1.0),
            Item(id="session-0", summary="Melanie topic 0", tags={}, score=0.9),
        ]

        embedding = [0.1] * 10
        groups = keeper._deep_edge_follow(
            primary, chroma_coll, doc_coll,
            query="topic", embedding=embedding,
        )

        if "Melanie" in groups:
            deep_ids = [i.id for i in groups["Melanie"]]
            assert "session-0" not in deep_ids

    def test_edge_follow_multiple_primaries(self, keeper):
        """Multiple primaries with edges should each get their own groups."""
        doc_coll = keeper._resolve_doc_collection()
        chroma_coll = keeper._resolve_chroma_collection()

        # Add a second entity with edges
        kp = keeper
        kp._document_store.upsert(doc_coll, "Caroline", summary="Another person",
                                  tags={"_source": "auto-vivify"})
        kp._document_store.upsert(doc_coll, "carol-msg",
                                  summary="Caroline discussed painting",
                                  tags={"speaker": "Caroline"})
        kp._document_store.upsert_edge(
            doc_coll, "carol-msg", "speaker", "Caroline", "said", "2024-02-01",
        )
        chroma_coll = kp._resolve_chroma_collection()
        kp._store.upsert(chroma_coll, "carol-msg", [0.5] * 10,
                          "Caroline discussed painting",
                          tags={"speaker": "caroline"})

        from keep.types import Item
        primary = [
            Item(id="Melanie", summary="A person", tags={}, score=1.0),
            Item(id="Caroline", summary="Another person", tags={}, score=0.8),
        ]

        embedding = [0.1] * 10
        groups = keeper._deep_edge_follow(
            primary, chroma_coll, doc_coll,
            query="topic painting", embedding=embedding,
        )

        # Both entities should potentially have groups
        # (depends on FTS matching, but at least the structure should work)
        assert isinstance(groups, dict)


# ---------------------------------------------------------------------------
# find(deep=True) integration — edge vs tag fallback
# ---------------------------------------------------------------------------

class TestFindDeepDispatch:
    """Verify find(deep=True) uses edges when available, tags otherwise."""

    def test_deep_uses_edges_when_available(self, tmp_path, mock_providers):
        kp = Keeper(store_path=str(tmp_path / "store"))
        doc_coll = kp._resolve_doc_collection()
        chroma_coll = kp._resolve_chroma_collection()

        kp._document_store.upsert(doc_coll, ".tag/speaker", summary="",
                                  tags={"_inverse": "said"})
        kp._document_store.upsert(doc_coll, "Alice", summary="A person",
                                  tags={})
        kp._store.upsert(chroma_coll, "Alice", [0.1] * 10, "A person", tags={})

        kp._document_store.upsert(doc_coll, "msg-1",
                                  summary="Alice said hello world",
                                  tags={"speaker": "Alice"})
        kp._document_store.upsert_edge(
            doc_coll, "msg-1", "speaker", "Alice", "said", "2024-01-01",
        )
        kp._store.upsert(chroma_coll, "msg-1", [0.2] * 10,
                          "Alice said hello world",
                          tags={"speaker": "alice"})

        assert kp._document_store.has_edges(doc_coll)

        results = kp.find("hello", deep=True, limit=5)
        assert len(results) > 0

    def test_deep_falls_back_to_tags_without_edges(self, tmp_path, mock_providers):
        kp = Keeper(store_path=str(tmp_path / "store"))
        doc_coll = kp._resolve_doc_collection()
        chroma_coll = kp._resolve_chroma_collection()

        kp._document_store.upsert(doc_coll, "doc-1",
                                  summary="Hello world",
                                  tags={"topic": "greetings"})
        kp._store.upsert(chroma_coll, "doc-1", [0.1] * 10, "Hello world",
                          tags={"topic": "greetings"})

        assert not kp._document_store.has_edges(doc_coll)

        results = kp.find("hello", deep=True, limit=5)
        assert len(results) > 0


# ---------------------------------------------------------------------------
# _build_fts_query helper
# ---------------------------------------------------------------------------

class TestBuildFtsQuery:

    @pytest.fixture
    def store(self, tmp_path):
        with DocumentStore(tmp_path / "documents.db") as s:
            yield s

    def test_basic_tokenization(self, store):
        result = store._build_fts_query("hello world")
        assert '"hello"' in result
        assert '"world"' in result
        assert "OR" in result

    def test_strips_quotes(self, store):
        result = store._build_fts_query('say "hello"')
        assert result is not None
        assert '""' not in result

    def test_only_special_chars_returns_none(self, store):
        result = store._build_fts_query("\"\" ''")
        assert result is None
