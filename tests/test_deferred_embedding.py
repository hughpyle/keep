"""Tests for deferred embedding in cloud mode.

When _is_local is False, Keeper.put() should skip embedding computation
and enqueue an "embed" task for the background worker instead.
"""

from pathlib import Path
from typing import Optional

import pytest

from keep.api import Keeper
from keep.pending_summaries import PendingSummary


# ---------------------------------------------------------------------------
# Helpers — tracking pending queue
# ---------------------------------------------------------------------------

class TrackingPendingQueue:
    """Pending queue that records enqueued tasks for assertions."""

    def __init__(self):
        self._items: list[dict] = []

    def enqueue(
        self, id: str, collection: str, content: str,
        *, task_type: str = "summarize", metadata: Optional[dict] = None,
    ) -> None:
        # Replace existing item with same (id, collection, task_type)
        # — matches real PendingSummaryQueue INSERT OR REPLACE behavior
        self._items = [
            i for i in self._items
            if not (i["id"] == id and i["collection"] == collection
                    and i["task_type"] == task_type)
        ]
        self._items.append({
            "id": id,
            "collection": collection,
            "content": content,
            "task_type": task_type,
            "metadata": metadata or {},
        })

    def dequeue(self, limit: int = 10) -> list[PendingSummary]:
        """Return enqueued items as PendingSummary for process_pending."""
        results = []
        for item in self._items[:limit]:
            results.append(PendingSummary(
                id=item["id"],
                collection=item["collection"],
                content=item["content"],
                queued_at="2026-01-01T00:00:00Z",
                task_type=item["task_type"],
                metadata=item["metadata"],
            ))
        return results

    def complete(self, id: str, collection: str, task_type: str = "summarize") -> None:
        self._items = [
            i for i in self._items
            if not (i["id"] == id and i["collection"] == collection
                    and i["task_type"] == task_type)
        ]

    def count(self) -> int:
        return len(self._items)

    def stats(self) -> dict:
        return {"pending": self.count()}

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    def get_status(self, id: str) -> dict | None:
        return None

    def close(self) -> None:
        self._items.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeferredEmbedding:
    """Cloud mode defers embedding to background worker."""

    def _make_keeper(self, mock_providers, tmp_path) -> tuple[Keeper, TrackingPendingQueue]:
        """Create a Keeper with injected stores (cloud mode: _is_local=False)."""
        from tests.conftest import MockChromaStore, MockDocumentStore

        doc_store = MockDocumentStore(tmp_path / "docs.db")
        vector_store = MockChromaStore(tmp_path)
        queue = TrackingPendingQueue()

        kp = Keeper(
            store_path=tmp_path,
            doc_store=doc_store,
            vector_store=vector_store,
            pending_queue=queue,
        )
        # Injected stores → _is_local=False (cloud path)
        assert not kp._is_local

        # Wire up the mock embedding provider and reset call counters
        # (Keeper init may trigger reconciliation that calls embed)
        embed_prov = mock_providers["embedding"]
        kp._embedding_provider = embed_prov
        kp._embedding_provider_loaded = True
        embed_prov.embed_calls = 0
        embed_prov.batch_calls = 0

        return kp, queue

    def test_put_defers_embedding(self, mock_providers, tmp_path):
        """put() should NOT call embed() in cloud mode; should enqueue 'embed' task."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)
        embed = mock_providers["embedding"]

        kp.put("hello world", id="test-note", tags={"topic": "test"})

        # Embedding was NOT called synchronously
        assert embed.embed_calls == 0

        # An embed task was enqueued
        assert queue.count() >= 1
        embed_tasks = [i for i in queue._items if i["task_type"] == "embed"]
        assert len(embed_tasks) == 1
        assert embed_tasks[0]["id"] == "test-note"
        assert embed_tasks[0]["content"] == "hello world"

    def test_put_writes_doc_store_immediately(self, mock_providers, tmp_path):
        """Doc store should have the record even before embedding runs."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)

        result = kp.put("some content", id="doc1", tags={"type": "note"})

        assert result is not None
        assert result.id == "doc1"
        # Doc is findable by ID
        doc = kp._document_store.get("default", "doc1")
        assert doc is not None
        assert doc.summary == "some content"

    def test_put_no_vector_store_entry_before_processing(self, mock_providers, tmp_path):
        """Vector store should NOT have the entry until background processes it."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)

        kp.put("some content", id="doc1")

        # Nothing in vector store yet
        vec = kp._store.get("default", "doc1")
        assert vec is None

    def test_process_pending_embed_writes_vector_store(self, mock_providers, tmp_path):
        """process_pending should compute embedding and write to vector store."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)
        embed = mock_providers["embedding"]

        # Put defers embedding
        kp.put("content for embedding", id="doc1")
        assert embed.embed_calls == 0

        # Process the queue
        result = kp.process_pending(limit=10)

        # Embedding was computed
        assert embed.embed_calls == 1

        # Vector store now has the entry
        chroma_coll = kp._resolve_chroma_collection()
        vec = kp._store.get(chroma_coll, "doc1")
        assert vec is not None

    def test_deferred_embed_content_change_archives_old(self, mock_providers, tmp_path):
        """When content changes, the old embedding should be archived as a version."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)
        embed = mock_providers["embedding"]

        # First put — creates initial doc + deferred embed
        kp.put("version one content", id="doc1")
        kp.process_pending(limit=10)
        assert embed.embed_calls == 1

        # Verify vector store has the embedding
        chroma_coll = kp._resolve_chroma_collection()
        old_embedding = kp._store.get_embedding(chroma_coll, "doc1")
        assert old_embedding is not None

        # Second put — different content
        kp.put("version two content, completely different", id="doc1")

        # Should have enqueued with content_changed=True
        embed_tasks = [i for i in queue._items if i["task_type"] == "embed"]
        assert len(embed_tasks) == 1
        assert embed_tasks[0]["metadata"].get("content_changed") is True

        # Process
        kp.process_pending(limit=10)
        assert embed.embed_calls == 2

        # New embedding is in vector store
        new_embedding = kp._store.get_embedding(chroma_coll, "doc1")
        assert new_embedding is not None

    def test_deferred_embed_idempotent_content(self, mock_providers, tmp_path):
        """Same content re-put should NOT set content_changed flag."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)

        kp.put("same content", id="doc1")
        kp.process_pending(limit=10)

        # Put same content again
        kp.put("same content", id="doc1")
        embed_tasks = [i for i in queue._items if i["task_type"] == "embed"]
        for task in embed_tasks:
            assert task["metadata"].get("content_changed") is not True

    def test_deleted_doc_skipped_by_embed_processor(self, mock_providers, tmp_path):
        """If doc is deleted before embed runs, the task should be a no-op."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)
        embed = mock_providers["embedding"]

        kp.put("content to delete", id="doc1")

        # Delete before processing
        kp.delete("doc1")

        # Process — should not crash, should not embed
        kp.process_pending(limit=10)
        assert embed.embed_calls == 0

    def test_multiple_puts_last_content_wins(self, mock_providers, tmp_path):
        """Multiple puts should result in last content being embedded."""
        kp, queue = self._make_keeper(mock_providers, tmp_path)
        embed = mock_providers["embedding"]

        kp.put("first version", id="doc1")
        kp.put("second version", id="doc1")

        # Process all pending
        kp.process_pending(limit=10)

        # Vector store should have entry
        chroma_coll = kp._resolve_chroma_collection()
        vec = kp._store.get(chroma_coll, "doc1")
        assert vec is not None


class TestLocalModeUnchanged:
    """Local mode should continue to embed synchronously."""

    def test_local_put_embeds_immediately(self, mock_providers, tmp_path):
        """In local mode (_is_local=True), put() should embed synchronously."""
        kp = Keeper(store_path=tmp_path)
        embed = mock_providers["embedding"]

        assert kp._is_local  # Factory-created stores → local mode

        # Trigger system doc migration with a throwaway put, then reset counter
        kp.put("warmup", id="_warmup")
        kp.delete("_warmup")
        embed.embed_calls = 0

        kp.put("hello world", id="doc1")

        # Embedding was called synchronously (exactly 1 call for the content)
        assert embed.embed_calls == 1

        # Vector store has the entry immediately
        chroma_coll = kp._resolve_chroma_collection()
        vec = kp._store.get(chroma_coll, "doc1")
        assert vec is not None


class TestNullPendingQueueSignature:
    """NullPendingQueue should accept task_type and metadata kwargs."""

    def test_enqueue_accepts_kwargs(self):
        from keep.backend import NullPendingQueue
        q = NullPendingQueue()
        # Should not raise
        q.enqueue("id", "coll", "content", task_type="embed", metadata={"key": "val"})
        assert q.count() == 0  # still no-ops
