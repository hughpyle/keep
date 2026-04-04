"""Concurrency and lifecycle tests for WorkQueue."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from keep.work_queue import WorkQueue


def test_concurrent_claim_no_overlap(tmp_path):
    """Two claimers should never receive the same work item."""
    queue = WorkQueue(tmp_path / "work.db")
    try:
        total = 20
        for i in range(total):
            queue.enqueue("tag", {"item_id": f"doc-{i}", "content": f"content {i}"})

        claimed_by_thread: list[list[str]] = [[], []]
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def worker(idx: int) -> None:
            try:
                barrier.wait(timeout=5)
                for _ in range(10):
                    batch = queue.claim(f"worker-{idx}", limit=2)
                    claimed_by_thread[idx].extend(item.work_id for item in batch)
                    for item in batch:
                        queue.complete(item.work_id, {"status": "ok"})
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(0,)),
            threading.Thread(target=worker, args=(1,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors, [repr(e) for e in errors]
        all_claimed = claimed_by_thread[0] + claimed_by_thread[1]
        assert len(all_claimed) == total
        assert len(all_claimed) == len(set(all_claimed))
        assert queue.count() == 0
    finally:
        queue.close()


def test_concurrent_producers_and_consumers_do_not_raise_sqlite_errors(tmp_path):
    """Concurrent enqueue/claim/complete should not hit SQLite transaction errors."""
    queue = WorkQueue(tmp_path / "work.db")
    try:
        producers = 2
        items_per_producer = 15
        total = producers * items_per_producer

        start_barrier = threading.Barrier(4)
        producers_done = threading.Event()
        errors: list[BaseException] = []
        seen_work_ids: set[str] = set()
        seen_lock = threading.Lock()
        processed = 0
        processed_lock = threading.Lock()

        def producer(idx: int) -> None:
            try:
                start_barrier.wait(timeout=5)
                for i in range(items_per_producer):
                    queue.enqueue(
                        "tag",
                        {"item_id": f"p{idx}-doc-{i}", "content": f"content {idx}-{i}"},
                    )
                    time.sleep(0.001)
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)

        def consumer(idx: int) -> None:
            nonlocal processed
            try:
                start_barrier.wait(timeout=5)
                while True:
                    batch = queue.claim(f"consumer-{idx}", limit=2)
                    if not batch:
                        if producers_done.is_set() and queue.count() == 0:
                            break
                        time.sleep(0.002)
                        continue
                    for item in batch:
                        with seen_lock:
                            assert item.work_id not in seen_work_ids
                            seen_work_ids.add(item.work_id)
                        queue.complete(item.work_id, {"status": "ok"})
                        with processed_lock:
                            processed += 1
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)

        producer_threads = [
            threading.Thread(target=producer, args=(0,)),
            threading.Thread(target=producer, args=(1,)),
        ]
        consumer_threads = [
            threading.Thread(target=consumer, args=(0,)),
            threading.Thread(target=consumer, args=(1,)),
        ]

        for thread in producer_threads + consumer_threads:
            thread.start()
        for thread in producer_threads:
            thread.join(timeout=10)
        producers_done.set()
        for thread in consumer_threads:
            thread.join(timeout=10)

        assert not errors, [repr(e) for e in errors]
        assert processed == total
        assert len(seen_work_ids) == total
        assert queue.count() == 0
    finally:
        queue.close()


def test_migrate_drops_orphaned_flow_engine_tables(tmp_path):
    """WorkQueue._migrate() drops the legacy FlowEngine tables."""
    db_path = tmp_path / "continuation.db"

    # Pre-create the legacy tables as the old FlowEngine would have.
    conn = sqlite3.connect(str(db_path))
    for table in (
        "continue_flows",
        "continue_events",
        "continue_mutations",
        "continue_idempotency",
    ):
        conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.execute(f"INSERT INTO {table} VALUES ('test')")
    conn.commit()
    conn.close()

    # Opening a WorkQueue triggers _init_db → _migrate, which should drop them.
    queue = WorkQueue(db_path)
    try:
        tables = {
            row[0]
            for row in queue._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "continue_work" in tables
        assert "continue_flows" not in tables
        assert "continue_events" not in tables
        assert "continue_mutations" not in tables
        assert "continue_idempotency" not in tables
    finally:
        queue.close()


def test_enable_auto_vacuum(tmp_path):
    """enable_auto_vacuum() enables auto_vacuum=FULL via one-time VACUUM."""
    db_path = tmp_path / "work.db"
    queue = WorkQueue(db_path)
    try:
        # Fresh DB starts without auto_vacuum.
        mode = queue._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        assert mode == 0

        # First call enables it.
        assert queue.enable_auto_vacuum() is True
        mode = queue._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        assert mode == 1

        # Second call is a no-op.
        assert queue.enable_auto_vacuum() is False
    finally:
        queue.close()

    # Persists across reopens.
    queue2 = WorkQueue(db_path)
    try:
        mode = queue2._conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        assert mode == 1
        assert queue2.enable_auto_vacuum() is False
    finally:
        queue2.close()


def test_auto_vacuum_reclaims_disk_space(tmp_path):
    """With auto_vacuum enabled, prune actually shrinks the file on disk."""
    db_path = tmp_path / "work.db"
    queue = WorkQueue(db_path)
    queue.enable_auto_vacuum()
    try:
        # Insert substantial data to make the size measurable.
        payload = {"data": "x" * 10000}
        ids = []
        for i in range(200):
            wid = queue.enqueue("tag", payload)
            ids.append(wid)
        items = queue.claim("w", limit=200)
        for item in items:
            queue.complete(item.work_id)

        size_before = db_path.stat().st_size

        # Backdate and prune.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        queue._conn.execute(
            "UPDATE continue_work SET updated_at = ?", (old_ts,),
        )
        deleted = queue.prune(keep_hours=24)
        assert deleted == 200

        size_after = db_path.stat().st_size
        assert size_after < size_before, (
            f"Expected file to shrink: {size_before} -> {size_after}"
        )
    finally:
        queue.close()


def test_prune_deletes_old_terminal_items(tmp_path):
    """prune() removes completed/superseded/dead_letter rows older than retention."""
    queue = WorkQueue(tmp_path / "work.db")
    try:
        # Enqueue and complete some work.
        ids = []
        for i in range(5):
            wid = queue.enqueue("tag", {"i": i})
            ids.append(wid)
        # Claim and complete all.
        items = queue.claim("w", limit=10)
        for item in items:
            queue.complete(item.work_id)

        # Backdate updated_at to 48 hours ago for 3 of them.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        for wid in ids[:3]:
            queue._conn.execute(
                "UPDATE continue_work SET updated_at = ? WHERE work_id = ?",
                (old_ts, wid),
            )

        # Prune with 24h retention — should delete the 3 old ones.
        deleted = queue.prune(keep_hours=24)
        assert deleted == 3

        # The 2 recent ones remain.
        remaining = queue._conn.execute(
            "SELECT COUNT(*) FROM continue_work"
        ).fetchone()[0]
        assert remaining == 2

        # Prune again — nothing more to delete.
        assert queue.prune(keep_hours=24) == 0
    finally:
        queue.close()


def test_prune_preserves_requested_items(tmp_path):
    """prune() must not touch items that are still in 'requested' status."""
    queue = WorkQueue(tmp_path / "work.db")
    try:
        queue.enqueue("tag", {"i": 0})

        # Backdate it.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        queue._conn.execute(
            "UPDATE continue_work SET updated_at = ?", (old_ts,),
        )

        # Prune should skip it — it's still requested.
        assert queue.prune(keep_hours=24) == 0
        assert queue.count() == 1
    finally:
        queue.close()
