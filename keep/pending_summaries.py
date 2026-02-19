"""
Pending work queue using SQLite.

Stores work items (summarization, analysis, etc.) for serial background
processing. This enables fast indexing with lazy summarization, and
ensures heavy ML work (MLX model loading) is serialized to prevent
memory exhaustion.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class PendingSummary:
    """A queued work item awaiting processing."""
    id: str
    collection: str
    content: str
    queued_at: str
    attempts: int = 0
    task_type: str = "summarize"
    metadata: dict = field(default_factory=dict)


class PendingSummaryQueue:
    """
    SQLite-backed queue for pending background work.

    Items are added during fast indexing (with truncated placeholder summary)
    or by enqueue_analyze, and processed later by `keep pending`
    or programmatically. All work is serialized to prevent concurrent
    ML model loading.
    """

    def __init__(self, queue_path: Path):
        """
        Args:
            queue_path: Path to SQLite database file
        """
        self._queue_path = queue_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._queue_path), check_same_thread=False)

        # Enable WAL mode for better concurrent access across processes
        self._conn.execute("PRAGMA journal_mode=WAL")
        # Wait up to 5 seconds for locks instead of failing immediately
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_summaries (
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
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queued_at
            ON pending_summaries(queued_at)
        """)
        self._conn.commit()

        # Migrate existing databases: add new columns if missing
        self._migrate()

    def _migrate(self) -> None:
        """Migrate existing databases to current schema."""
        cursor = self._conn.execute("PRAGMA table_info(pending_summaries)")
        columns = {row[1] for row in cursor.fetchall()}

        if "task_type" not in columns:
            # Old schema: PK is (id, collection). Recreate with (id, collection, task_type).
            # Preserve any pending items during migration.
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_summaries_new (
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
            self._conn.execute("""
                INSERT OR IGNORE INTO pending_summaries_new
                    (id, collection, content, queued_at, attempts)
                SELECT id, collection, content, queued_at, attempts
                FROM pending_summaries
            """)
            self._conn.execute("DROP TABLE pending_summaries")
            self._conn.execute(
                "ALTER TABLE pending_summaries_new RENAME TO pending_summaries"
            )
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queued_at
                ON pending_summaries(queued_at)
            """)
            self._conn.commit()

    def enqueue(
        self,
        id: str,
        collection: str,
        content: str,
        *,
        task_type: str = "summarize",
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Add an item to the pending queue.

        If the same (id, collection, task_type) already exists, replaces it.
        Different task types for the same document coexist independently.
        """
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else "{}"
        with self._lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO pending_summaries
                (id, collection, content, queued_at, attempts, task_type, metadata)
                VALUES (?, ?, ?, ?, 0, ?, ?)
            """, (id, collection, content, now, task_type, meta_json))
            self._conn.commit()

    def dequeue(self, limit: int = 10) -> list[PendingSummary]:
        """
        Get the oldest pending items for processing.

        Items are returned but not removed - call `complete()` after successful processing.
        Increments attempt counter on each dequeue.
        """
        with self._lock:
            cursor = self._conn.execute("""
                SELECT id, collection, content, queued_at, attempts, task_type, metadata
                FROM pending_summaries
                ORDER BY queued_at ASC
                LIMIT ?
            """, (limit,))

            items = []
            for row in cursor.fetchall():
                meta = {}
                if row[6]:
                    try:
                        meta = json.loads(row[6])
                    except (json.JSONDecodeError, TypeError):
                        pass
                items.append(PendingSummary(
                    id=row[0],
                    collection=row[1],
                    content=row[2],
                    queued_at=row[3],
                    attempts=row[4],
                    task_type=row[5] or "summarize",
                    metadata=meta,
                ))

            # Increment attempt counters
            if items:
                keys = [
                    (item.id, item.collection, item.task_type)
                    for item in items
                ]
                self._conn.executemany("""
                    UPDATE pending_summaries
                    SET attempts = attempts + 1
                    WHERE id = ? AND collection = ? AND task_type = ?
                """, keys)
                self._conn.commit()

        return items

    def complete(
        self, id: str, collection: str, task_type: str = "summarize"
    ) -> None:
        """Remove an item from the queue after successful processing."""
        with self._lock:
            self._conn.execute("""
                DELETE FROM pending_summaries
                WHERE id = ? AND collection = ? AND task_type = ?
            """, (id, collection, task_type))
            self._conn.commit()

    def fail(
        self, id: str, collection: str, task_type: str = "summarize",
        error: str | None = None,
    ) -> None:
        """Record failure but leave in queue for retry.

        The item stays in the queue with its bumped attempts counter.
        Next dequeue will pick it up again (unless attempts >= MAX).
        For SQLite (local mode) this is a no-op — items are already
        retryable since dequeue doesn't change their status.
        """
        pass  # SQLite queue has no status column; retry is automatic

    def count(self) -> int:
        """Get count of pending items."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM pending_summaries")
        return cursor.fetchone()[0]

    def stats(self) -> dict:
        """Get queue statistics."""
        cursor = self._conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT collection) as collections,
                MAX(attempts) as max_attempts,
                MIN(queued_at) as oldest
            FROM pending_summaries
        """)
        row = cursor.fetchone()
        return {
            "pending": row[0],
            "collections": row[1],
            "max_attempts": row[2] or 0,
            "oldest": row[3],
            "queue_path": str(self._queue_path),
            "by_type": self.stats_by_type(),
        }

    def stats_by_type(self) -> dict[str, int]:
        """Get count of pending items grouped by task type."""
        cursor = self._conn.execute("""
            SELECT task_type, COUNT(*) as cnt
            FROM pending_summaries
            GROUP BY task_type
            ORDER BY cnt DESC
        """)
        return {row[0]: row[1] for row in cursor.fetchall()}

    def get_status(self, id: str) -> dict | None:
        """Get pending task status for a specific note.

        Returns dict with id, task_type, status, queued_at if the note
        has pending work, or None if no work is pending.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, task_type, queued_at FROM pending_summaries WHERE id = ?",
                (id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "task_type": row[1],
            "status": "queued",
            "queued_at": row[2],
        }

    def clear(self) -> int:
        """Clear all pending items. Returns count of items cleared."""
        with self._lock:
            count = self.count()
            self._conn.execute("DELETE FROM pending_summaries")
            self._conn.commit()
        return count

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        """Ensure connection is closed on garbage collection."""
        self.close()
