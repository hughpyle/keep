"""
Tests for tag-driven edges (_inverse on tagdocs).

Edge lifecycle:
- A tagdoc `.tag/KEY` with `_inverse: VERB` makes KEY an edge-tag
- When doc-X has `KEY: target_id`, an edge row is created
- `get target_id` shows inverse edges under the VERB heading
- Auto-vivification: targets that don't exist are created as empty docs
- Deleting source/target cleans up edges
- Changing _inverse triggers backfill/cleanup
"""

import pytest
from pathlib import Path

from keep.api import Keeper
from keep.document_store import DocumentStore
from keep.types import EdgeRef


# ---------------------------------------------------------------------------
# DocumentStore edge CRUD (real SQLite)
# ---------------------------------------------------------------------------

class TestDocumentStoreEdges:
    """Edge table CRUD on a real SQLite database."""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "documents.db"
        with DocumentStore(db_path) as s:
            yield s

    def test_upsert_and_get_inverse_edges(self, store):
        store.upsert_edge("default", "conv1@v5", "speaker", "nate", "said", "2025-01-01T00:00:00")
        store.upsert_edge("default", "conv1@v12", "speaker", "nate", "said", "2025-01-02T00:00:00")

        edges = store.get_inverse_edges("default", "nate")
        assert len(edges) == 2
        # Ordered by (inverse, created DESC)
        assert edges[0] == ("said", "conv1@v12", "2025-01-02T00:00:00")
        assert edges[1] == ("said", "conv1@v5", "2025-01-01T00:00:00")

    def test_upsert_edge_replaces_on_same_pk(self, store):
        store.upsert_edge("default", "doc1", "speaker", "alice", "said", "2025-01-01T00:00:00")
        store.upsert_edge("default", "doc1", "speaker", "bob", "said", "2025-01-02T00:00:00")

        # PK is (source_id, collection, predicate) — only one row
        edges_alice = store.get_inverse_edges("default", "alice")
        edges_bob = store.get_inverse_edges("default", "bob")
        assert len(edges_alice) == 0
        assert len(edges_bob) == 1

    def test_delete_edges_for_source(self, store):
        store.upsert_edge("default", "doc1", "speaker", "nate", "said", "2025-01-01T00:00:00")
        store.upsert_edge("default", "doc1", "topic", "ai", "discussed_in", "2025-01-01T00:00:00")
        assert store.delete_edges_for_source("default", "doc1") == 2
        assert store.get_inverse_edges("default", "nate") == []

    def test_delete_edges_for_target(self, store):
        store.upsert_edge("default", "doc1", "speaker", "nate", "said", "2025-01-01T00:00:00")
        store.upsert_edge("default", "doc2", "speaker", "nate", "said", "2025-01-02T00:00:00")
        assert store.delete_edges_for_target("default", "nate") == 2
        assert store.get_inverse_edges("default", "nate") == []

    def test_delete_edges_for_predicate(self, store):
        store.upsert_edge("default", "doc1", "speaker", "nate", "said", "2025-01-01T00:00:00")
        store.upsert_edge("default", "doc2", "speaker", "bob", "said", "2025-01-02T00:00:00")
        assert store.delete_edges_for_predicate("default", "speaker") == 2

    def test_backfill_lifecycle(self, store):
        # Not found initially
        assert store.get_backfill_status("default", "speaker") is None

        # Create pending backfill
        store.upsert_backfill("default", "speaker", "said")
        assert store.get_backfill_status("default", "speaker") is None  # completed is NULL

        # Mark complete
        store.upsert_backfill("default", "speaker", "said", completed="2025-01-01T00:00:00")
        assert store.get_backfill_status("default", "speaker") == "2025-01-01T00:00:00"

        # Delete
        store.delete_backfill("default", "speaker")
        assert store.get_backfill_status("default", "speaker") is None

    def test_no_edges_returns_empty(self, store):
        assert store.get_inverse_edges("default", "nonexistent") == []

    def test_cross_collection_isolation(self, store):
        store.upsert_edge("coll_a", "doc1", "speaker", "nate", "said", "2025-01-01T00:00:00")
        assert store.get_inverse_edges("coll_b", "nate") == []
        assert len(store.get_inverse_edges("coll_a", "nate")) == 1


# ---------------------------------------------------------------------------
# Integration: Keeper edge processing with mocks
# ---------------------------------------------------------------------------

class TestEdgeIntegration:
    """End-to-end edge creation/deletion through Keeper."""

    @pytest.fixture
    def kp(self, mock_providers, tmp_path):
        return Keeper(store_path=tmp_path)

    def _create_tagdoc(self, kp, key, inverse):
        """Create a .tag/KEY tagdoc with _inverse.

        Uses direct document_store write (like system_docs.py) because
        _inverse is a system tag that gets filtered by put().
        """
        from keep.types import utc_now
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()
        tags = {
            "_inverse": inverse,
            "_created": now,
            "_updated": now,
            "_source": "inline",
            "category": "system",
        }
        kp._document_store.upsert(
            collection=doc_coll,
            id=f".tag/{key}",
            summary=f"Tag: {key}",
            tags=tags,
        )

    def test_edge_created_on_put(self, kp):
        """Tag with _inverse tagdoc → edge in table → get target shows inverse."""
        self._create_tagdoc(kp, "speaker", "said")

        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        ctx = kp.get_context("nate")
        assert ctx is not None
        assert "said" in ctx.edges
        assert len(ctx.edges["said"]) == 1
        assert ctx.edges["said"][0].source_id == "conv1"

    def test_auto_vivification(self, kp):
        """Target that doesn't exist is created as empty doc."""
        self._create_tagdoc(kp, "speaker", "said")

        # "nate" doesn't exist yet
        assert kp.get("nate") is None

        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        # Now "nate" should exist (auto-vivified)
        item = kp.get("nate")
        assert item is not None
        assert item.tags.get("_source") == "auto-vivify"

    def test_multiple_edges_same_target(self, kp):
        """Multiple docs pointing at the same target show as multiple edges."""
        self._create_tagdoc(kp, "speaker", "said")

        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})
        kp.put(content="Nate said goodbye", id="conv2", summary="Farewell",
               tags={"speaker": "nate"})

        ctx = kp.get_context("nate")
        assert len(ctx.edges["said"]) == 2
        source_ids = {e.source_id for e in ctx.edges["said"]}
        assert source_ids == {"conv1", "conv2"}

    def test_delete_source_cleans_edges(self, kp):
        """Deleting source doc removes its edges."""
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        kp.delete("conv1")

        ctx = kp.get_context("nate")
        assert ctx.edges.get("said", []) == []

    def test_delete_target_cleans_inverse_edges(self, kp):
        """Deleting target doc removes edges pointing at it."""
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        kp.delete("nate")

        # Edge should be gone even though source still exists
        # (re-create nate to check)
        kp.put(content="", id="nate", summary="")
        ctx = kp.get_context("nate")
        assert ctx.edges.get("said", []) == []

    def test_tag_value_change_updates_edge(self, kp):
        """Changing tag value removes old edge and creates new one."""
        self._create_tagdoc(kp, "speaker", "said")

        kp.put(content="Someone said hello", id="conv1", summary="Greeting",
               tags={"speaker": "alice"})

        # alice has the edge
        ctx_alice = kp.get_context("alice")
        assert len(ctx_alice.edges.get("said", [])) == 1

        # Change speaker from alice to bob
        kp.put(content="Someone said hello", id="conv1", summary="Greeting",
               tags={"speaker": "bob"})

        # bob gets the edge, alice loses it
        ctx_bob = kp.get_context("bob")
        assert len(ctx_bob.edges.get("said", [])) == 1
        ctx_alice = kp.get_context("alice")
        assert ctx_alice.edges.get("said", []) == []

    def test_removing_one_edge_tag_preserves_others(self, kp):
        """Removing one edge tag must not delete edges from other predicates."""
        self._create_tagdoc(kp, "speaker", "said")
        self._create_tagdoc(kp, "location", "visited_by")

        kp.put(content="Meeting", id="conv1", summary="Meeting notes",
               tags={"speaker": "alice", "location": "office"})

        # Both edges exist
        ctx_alice = kp.get_context("alice")
        assert len(ctx_alice.edges.get("said", [])) == 1
        ctx_office = kp.get_context("office")
        assert len(ctx_office.edges.get("visited_by", [])) == 1

        # Remove speaker tag (set to "") but keep location
        kp.put(content="Meeting", id="conv1", summary="Meeting notes",
               tags={"speaker": "", "location": "office"})

        # office edge should survive
        ctx_office = kp.get_context("office")
        assert len(ctx_office.edges.get("visited_by", [])) == 1
        # alice edge should be gone
        ctx_alice = kp.get_context("alice")
        assert ctx_alice.edges.get("said", []) == []

    def test_sysdoc_target_skipped(self, kp):
        """Targets starting with '.' (sysdoc names) don't create edges."""
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="System ref", id="doc1", summary="Ref",
               tags={"speaker": ".meta/todo"})

        # No edge should exist for .meta/todo
        doc_coll = kp._resolve_doc_collection()
        edges = kp._document_store.get_inverse_edges(doc_coll, ".meta/todo")
        assert edges == []

    def test_no_edge_without_inverse_tagdoc(self, kp):
        """Tags without _inverse tagdoc don't create edges."""
        kp.put(content="Some doc", id="doc1", summary="Doc",
               tags={"topic": "ai"})

        # "ai" should not have edges
        kp.put(content="", id="ai", summary="AI topic")
        ctx = kp.get_context("ai")
        assert ctx.edges == {}

    def test_inverse_removal_cleans_edges(self, kp):
        """Removing _inverse from tagdoc cleans up all edges for that predicate."""
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="Nate said hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        # Verify edge exists
        ctx = kp.get_context("nate")
        assert len(ctx.edges.get("said", [])) == 1

        # Remove _inverse from tagdoc by rewriting without it (direct store write)
        from keep.types import utc_now
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()
        new_tags = {"_created": now, "_updated": now, "_source": "inline"}
        # Get old tags before overwriting
        old_doc = kp._document_store.get(doc_coll, ".tag/speaker")
        old_tags = old_doc.tags if old_doc else {}
        # Detect inverse change before storage
        kp._process_tagdoc_inverse_change(
            ".tag/speaker", new_tags, old_tags, doc_coll,
        )
        kp._document_store.upsert(
            collection=doc_coll,
            id=".tag/speaker",
            summary="Tag: speaker",
            tags=new_tags,
        )

        ctx = kp.get_context("nate")
        assert ctx.edges.get("said", []) == []

    def test_get_context_includes_edge_summaries(self, kp):
        """EdgeRefs include the source document's summary."""
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="Nate said something interesting", id="conv1",
               summary="Interesting remark", tags={"speaker": "nate"})

        ctx = kp.get_context("nate")
        assert ctx.edges["said"][0].summary == "Interesting remark"

    def test_get_context_no_edges_when_none(self, kp):
        """get_context returns empty edges dict for doc with no inverse edges."""
        kp.put(content="Just a normal doc", id="doc1", summary="Normal doc")
        ctx = kp.get_context("doc1")
        assert ctx.edges == {}


# ---------------------------------------------------------------------------
# ItemContext serialization
# ---------------------------------------------------------------------------

class TestEdgeRefSerialization:
    """EdgeRef round-trips through to_dict/from_dict."""

    def _create_tagdoc(self, kp, key, inverse):
        from keep.types import utc_now
        doc_coll = kp._resolve_doc_collection()
        now = utc_now()
        kp._document_store.upsert(
            collection=doc_coll, id=f".tag/{key}",
            summary=f"Tag: {key}",
            tags={"_inverse": inverse, "_created": now, "_updated": now, "_source": "inline"},
        )

    def test_round_trip(self, mock_providers, tmp_path):
        kp = Keeper(store_path=tmp_path)
        self._create_tagdoc(kp, "speaker", "said")
        kp.put(content="Hello", id="conv1", summary="Greeting",
               tags={"speaker": "nate"})

        ctx = kp.get_context("nate")
        d = ctx.to_dict()
        ctx2 = type(ctx).from_dict(d)
        assert ctx2.edges == ctx.edges
        assert len(ctx2.edges["said"]) == 1
        assert ctx2.edges["said"][0].source_id == "conv1"
