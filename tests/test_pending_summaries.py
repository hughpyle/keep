"""Tests for pending summaries queue."""

import tempfile
from pathlib import Path

from keep.pending_summaries import PendingSummaryQueue


class TestPendingSummaryQueue:
    """Tests for the SQLite-backed pending summary queue."""

    def test_enqueue_and_count(self):
        """Should enqueue items and track count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            assert queue.count() == 0

            queue.enqueue("doc1", "default", "content one")
            assert queue.count() == 1

            queue.enqueue("doc2", "default", "content two")
            assert queue.count() == 2

            queue.close()

    def test_dequeue_returns_oldest_first(self):
        """Should return items in FIFO order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("first", "default", "content first")
            queue.enqueue("second", "default", "content second")
            queue.enqueue("third", "default", "content third")

            items = queue.dequeue(limit=2)
            assert len(items) == 2
            assert items[0].id == "first"
            assert items[1].id == "second"

            queue.close()

    def test_dequeue_increments_attempts(self):
        """Should increment attempt counter on dequeue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")

            items = queue.dequeue(limit=1)
            assert items[0].attempts == 0  # Was 0 before dequeue

            # Dequeue again (item still there since not completed)
            items = queue.dequeue(limit=1)
            assert items[0].attempts == 1  # Incremented

            queue.close()

    def test_complete_removes_item(self):
        """Should remove item from queue on complete."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            assert queue.count() == 1

            queue.complete("doc1", "default")
            assert queue.count() == 0

            queue.close()

    def test_enqueue_replaces_existing(self):
        """Should replace existing item with same id+collection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "original content")
            queue.enqueue("doc1", "default", "updated content")

            assert queue.count() == 1

            items = queue.dequeue(limit=1)
            assert items[0].content == "updated content"

            queue.close()

    def test_separate_collections(self):
        """Should treat same id in different collections as separate items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "collection_a", "content a")
            queue.enqueue("doc1", "collection_b", "content b")

            assert queue.count() == 2

            queue.close()

    def test_stats(self):
        """Should return queue statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "coll_a", "content")
            queue.enqueue("doc2", "coll_b", "content")

            stats = queue.stats()
            assert stats["pending"] == 2
            assert stats["collections"] == 2
            assert "queue_path" in stats

            queue.close()

    def test_clear(self):
        """Should clear all pending items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.enqueue("doc2", "default", "content")

            cleared = queue.clear()
            assert cleared == 2
            assert queue.count() == 0

            queue.close()
