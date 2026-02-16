"""
Tests for KeepNotesRetriever.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper

pytest.importorskip("langchain_core")

from keep.langchain.retriever import KeepNotesRetriever


@pytest.fixture
def keeper(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


@pytest.fixture
def retriever(keeper):
    # limit=30 to ensure mock store (insertion-order, not similarity-ranked)
    # returns enough items to reach past the ~26 system docs.
    return KeepNotesRetriever(keeper=keeper, limit=30)


@pytest.fixture
def retriever_with_now(keeper):
    return KeepNotesRetriever(keeper=keeper, limit=30, include_now=True)


@pytest.fixture
def scoped_retriever(keeper):
    return KeepNotesRetriever(keeper=keeper, user_id="alice", limit=30)


class TestRetrieverBasic:

    def test_invoke_returns_documents(self, retriever, keeper):
        keeper.put("Python is great for scripting", id="note:1")
        docs = retriever.invoke("python scripting")
        assert len(docs) > 0
        assert all(hasattr(d, "page_content") for d in docs)
        assert all(hasattr(d, "metadata") for d in docs)

    def test_invoke_empty_store(self, retriever):
        docs = retriever.invoke("anything")
        assert docs == []

    def test_metadata_includes_source(self, retriever, keeper):
        keeper.put("Test note", id="note:1")
        docs = retriever.invoke("test")
        assert docs[0].metadata["source"] == "note:1"

    def test_metadata_includes_user_tags(self, retriever, keeper):
        keeper.put("Tagged note", id="note:1", tags={"topic": "testing"})
        docs = retriever.invoke("tagged")
        assert docs[0].metadata.get("topic") == "testing"

    def test_metadata_excludes_system_tags(self, retriever, keeper):
        keeper.put("Note with system tags", id="note:1")
        docs = retriever.invoke("system tags")
        for key in docs[0].metadata:
            assert not key.startswith("_") or key == "source" or key == "score"


class TestRetrieverWithNow:

    def test_now_prepended_when_set(self, retriever_with_now, keeper):
        keeper.set_now("Currently reviewing code")
        keeper.put("Some other note", id="note:1")
        docs = retriever_with_now.invoke("review")
        # First doc should be the now context
        assert docs[0].metadata.get("type") == "now"
        assert "reviewing" in docs[0].page_content.lower()

    def test_now_not_prepended_when_false(self, retriever, keeper):
        keeper.set_now("Active context")
        keeper.put("Some note", id="note:1")
        docs = retriever.invoke("note")
        # No now doc should be present
        types = [d.metadata.get("type") for d in docs]
        assert "now" not in types


class TestRetrieverScoped:

    def test_scoped_retriever_filters_by_user(self, scoped_retriever, keeper):
        keeper.put("Alice's note", id="note:a", tags={"user": "alice"})
        keeper.put("Bob's note", id="note:b", tags={"user": "bob"})
        docs = scoped_retriever.invoke("note")
        # Should find alice's note (scoped)
        assert len(docs) > 0
