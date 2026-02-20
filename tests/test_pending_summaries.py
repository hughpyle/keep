"""Tests for pending summaries queue."""

import tempfile
import threading
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

    def test_dequeue_claims_items(self):
        """Dequeued items should not appear in subsequent dequeue calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content one")
            queue.enqueue("doc2", "default", "content two")

            # First dequeue claims doc1
            items1 = queue.dequeue(limit=1)
            assert len(items1) == 1
            assert items1[0].id == "doc1"

            # Second dequeue should get doc2, not doc1 again
            items2 = queue.dequeue(limit=1)
            assert len(items2) == 1
            assert items2[0].id == "doc2"

            # Nothing left
            items3 = queue.dequeue(limit=1)
            assert len(items3) == 0

            queue.close()

    def test_dequeue_increments_attempts(self):
        """Should increment attempt counter on dequeue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")

            items = queue.dequeue(limit=1)
            assert items[0].attempts == 0  # Was 0 before dequeue

            # Release it back via fail()
            queue.fail("doc1", "default")

            # Dequeue again — attempt counter should be incremented
            items = queue.dequeue(limit=1)
            assert items[0].attempts == 1

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

    def test_fail_releases_item(self):
        """fail() should release a claimed item back to pending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")

            # Claim it
            items = queue.dequeue(limit=1)
            assert len(items) == 1
            assert queue.count() == 0  # Not pending anymore

            # Fail it — should be pending again
            queue.fail("doc1", "default")
            assert queue.count() == 1

            # Can dequeue again
            items = queue.dequeue(limit=1)
            assert len(items) == 1
            assert items[0].id == "doc1"

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

    def test_enqueue_resets_claimed_item(self):
        """Re-enqueueing a claimed item should reset it to pending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "original")
            queue.dequeue(limit=1)  # Claims it
            assert queue.count() == 0  # Processing, not pending

            # Re-enqueue resets to pending
            queue.enqueue("doc1", "default", "updated")
            assert queue.count() == 1

            items = queue.dequeue(limit=1)
            assert items[0].content == "updated"

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

    def test_concurrent_dequeue_no_overlap(self):
        """Two threads calling dequeue() should never get the same item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            # Enqueue 10 items
            for i in range(10):
                queue.enqueue(f"doc{i}", "default", f"content {i}")

            results = [[], []]
            barrier = threading.Barrier(2)

            def dequeue_worker(idx):
                barrier.wait()  # Synchronize start
                items = queue.dequeue(limit=10)
                results[idx] = [item.id for item in items]

            t1 = threading.Thread(target=dequeue_worker, args=(0,))
            t2 = threading.Thread(target=dequeue_worker, args=(1,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # No overlap: each item claimed by exactly one thread
            all_claimed = results[0] + results[1]
            assert len(all_claimed) == len(set(all_claimed)), \
                f"Overlap detected: {results[0]} vs {results[1]}"
            assert len(all_claimed) == 10

            queue.close()

    def test_get_status_reflects_claim(self):
        """get_status should show processing status for claimed items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            status = queue.get_status("doc1")
            assert status["status"] == "pending"

            queue.dequeue(limit=1)
            status = queue.get_status("doc1")
            assert status["status"] == "processing"

            queue.close()

    def test_count_excludes_processing(self):
        """count() should only count pending items, not processing ones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content one")
            queue.enqueue("doc2", "default", "content two")
            assert queue.count() == 2

            queue.dequeue(limit=1)
            assert queue.count() == 1  # One still pending

            queue.close()

    def test_migration_adds_status_columns(self):
        """Opening an old database without status columns should migrate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "pending.db"

            # Create old-schema database manually
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE pending_summaries (
                    id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    content TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    task_type TEXT DEFAULT 'summarize',
                    metadata TEXT DEFAULT '{}',
                    PRIMARY KEY (id, collection, task_type)
                )
            """)
            conn.execute("""
                INSERT INTO pending_summaries (id, collection, content, queued_at)
                VALUES ('old_doc', 'default', 'old content', '2025-01-01T00:00:00')
            """)
            conn.commit()
            conn.close()

            # Open with new code — should migrate
            queue = PendingSummaryQueue(db_path)

            # Old item should be accessible and pending
            items = queue.dequeue(limit=1)
            assert len(items) == 1
            assert items[0].id == "old_doc"

            queue.close()
