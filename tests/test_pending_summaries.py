"""Tests for pending summaries queue."""

import tempfile
import threading
from datetime import datetime, timedelta, timezone
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

            # Clear retry_after so it's immediately available
            queue._conn.execute(
                "UPDATE pending_summaries SET retry_after = NULL WHERE id = 'doc1'"
            )
            queue._conn.commit()

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

            # Fail it — should be pending again (with retry backoff)
            queue.fail("doc1", "default")
            assert queue.count() == 1

            # Clear retry_after so it's immediately available
            queue._conn.execute(
                "UPDATE pending_summaries SET retry_after = NULL WHERE id = 'doc1'"
            )
            queue._conn.commit()

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

    def test_fail_sets_retry_backoff(self):
        """fail() should set retry_after in the future, blocking immediate dequeue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.dequeue(limit=1)

            # Fail it — sets retry_after ~30s in the future
            queue.fail("doc1", "default", error="test error")

            # Item is pending but backoff hasn't elapsed
            assert queue.count() == 1

            # Immediate dequeue should return nothing (backoff active)
            items = queue.dequeue(limit=1)
            assert len(items) == 0

            queue.close()

    def test_fail_stores_error_message(self):
        """fail() should store the error message for diagnosis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.dequeue(limit=1)
            queue.fail("doc1", "default", error="RuntimeError: model crashed")

            # Check error is stored
            cursor = queue._conn.execute(
                "SELECT last_error FROM pending_summaries WHERE id = 'doc1'"
            )
            row = cursor.fetchone()
            assert row[0] == "RuntimeError: model crashed"

            queue.close()

    def test_fail_backoff_increases_exponentially(self):
        """Successive failures should increase the retry delay."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")

            retry_afters = []
            for i in range(3):
                # Clear backoff to allow dequeue
                queue._conn.execute(
                    "UPDATE pending_summaries SET retry_after = NULL WHERE id = 'doc1'"
                )
                queue._conn.commit()

                queue.dequeue(limit=1)
                queue.fail("doc1", "default", error=f"fail {i}")

                cursor = queue._conn.execute(
                    "SELECT retry_after FROM pending_summaries WHERE id = 'doc1'"
                )
                retry_afters.append(cursor.fetchone()[0])

            # Each retry_after should be further in the future
            # (30s, 60s, 120s from roughly the same "now")
            for i in range(1, len(retry_afters)):
                assert retry_afters[i] > retry_afters[i - 1], \
                    f"Backoff should increase: {retry_afters}"

            queue.close()

    def test_abandon_moves_to_failed_status(self):
        """abandon() should move item to 'failed' (dead letter)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.dequeue(limit=1)

            queue.abandon("doc1", "default", error="Exhausted 5 attempts")

            # Not pending, not available for dequeue
            assert queue.count() == 0
            items = queue.dequeue(limit=1)
            assert len(items) == 0

            # But preserved in failed list
            failed = queue.list_failed()
            assert len(failed) == 1
            assert failed[0]["id"] == "doc1"
            assert failed[0]["last_error"] == "Exhausted 5 attempts"

            queue.close()

    def test_retry_failed_resets_to_pending(self):
        """retry_failed() should reset dead-letter items back to pending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.dequeue(limit=1)
            queue.abandon("doc1", "default", error="gave up")

            assert queue.count() == 0
            assert len(queue.list_failed()) == 1

            # Retry resets them
            n = queue.retry_failed()
            assert n == 1
            assert queue.count() == 1
            assert len(queue.list_failed()) == 0

            # Can dequeue again
            items = queue.dequeue(limit=1)
            assert len(items) == 1
            assert items[0].id == "doc1"
            assert items[0].attempts == 0  # Reset

            queue.close()

    def test_stats_includes_status_breakdown(self):
        """stats() should include pending, processing, and failed counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            queue = PendingSummaryQueue(Path(tmpdir) / "pending.db")

            queue.enqueue("doc1", "default", "content")
            queue.enqueue("doc2", "default", "content")
            queue.enqueue("doc3", "default", "content")

            # doc1 → processing
            queue.dequeue(limit=1)
            # doc2 → failed
            queue._conn.execute(
                "UPDATE pending_summaries SET status = 'failed' WHERE id = 'doc2'"
            )
            queue._conn.commit()

            stats = queue.stats()
            assert stats["pending"] == 1     # doc3
            assert stats["processing"] == 1  # doc1
            assert stats["failed"] == 1      # doc2
            assert stats["total"] == 3

            queue.close()
