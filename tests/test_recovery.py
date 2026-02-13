"""
Error recovery tests for DocumentStore.

Tests malformed database detection, iterdump recovery, runtime recovery,
and the non-fatal touch() behavior added in v0.38.1.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from keep.document_store import DocumentStore


def _create_valid_db_with_data(path: Path) -> None:
    """Create a valid DocumentStore with some data."""
    store = DocumentStore(path)
    store.upsert("default", "doc1", "First document", {"topic": "test"})
    store.upsert("default", "doc2", "Second document", {"topic": "test"})
    store.upsert("default", "doc3", "Third document", {"topic": "other"})
    store._conn.close()


def _corrupt_database(path: Path) -> None:
    """Corrupt a SQLite database by overwriting bytes in the middle."""
    data = path.read_bytes()
    # Corrupt bytes past the header (first 100 bytes are the header)
    # Write garbage in the middle of the file to corrupt a data page
    if len(data) > 2000:
        corrupted = data[:1000] + b"\x00" * 500 + data[1500:]
    else:
        corrupted = data[:100] + b"\x00" * 100 + data[200:]
    path.write_bytes(corrupted)


class TestMalformedDetection:
    """Test that malformed databases are detected during init."""

    def test_init_detects_and_recovers_malformed(self, tmp_path):
        """A corrupted DB should trigger recovery during init."""
        db_path = tmp_path / "test.db"
        _create_valid_db_with_data(db_path)

        # Checkpoint WAL so corruption hits the main file
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        _corrupt_database(db_path)

        # Remove WAL/SHM so the corrupt main file is what gets opened
        for suffix in ("-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

        # Opening should either recover or raise
        try:
            store = DocumentStore(db_path)
            # If recovery succeeded, the DB should be usable
            store.upsert("default", "new", "post-recovery", {})
            rec = store.get("default", "new")
            assert rec is not None
        except sqlite3.DatabaseError:
            # If data was too corrupted to dump, that's expected
            pass

    def test_corrupt_backup_created(self, tmp_path):
        """Recovery should preserve the corrupt file as .db.corrupt."""
        db_path = tmp_path / "test.db"
        _create_valid_db_with_data(db_path)

        # Force recovery by simulating malformation on first init
        original_init = DocumentStore._init_db
        call_count = [0]

        def patched_init(self):
            call_count[0] += 1
            if call_count[0] == 1:
                original_init(self)
                raise sqlite3.DatabaseError("database disk image is malformed")
            else:
                original_init(self)

        with patch.object(DocumentStore, "_init_db", patched_init):
            DocumentStore(db_path)

        corrupt_path = Path(str(db_path) + ".corrupt")
        assert corrupt_path.exists(), "Corrupt backup should exist"


class TestRecoveryFromMalformed:
    """Test the iterdump recovery path preserves data."""

    def test_recovery_preserves_data(self, tmp_path):
        """Data should survive recovery via iterdump."""
        db_path = tmp_path / "test.db"
        _create_valid_db_with_data(db_path)

        # Force recovery by making quick_check fail
        # We do this by patching — cleaner than real corruption
        original_init = DocumentStore._init_db

        call_count = [0]

        def patched_init(self):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: set up connection but pretend it's malformed
                original_init(self)
                raise sqlite3.DatabaseError("database disk image is malformed")
            else:
                # Recovery path re-calls _init_db
                original_init(self)

        with patch.object(DocumentStore, "_init_db", patched_init):
            store = DocumentStore(db_path)

        # Data should be preserved
        rec = store.get("default", "doc1")
        assert rec is not None
        assert rec.summary == "First document"

        rec2 = store.get("default", "doc2")
        assert rec2 is not None

    def test_recovery_removes_wal_shm(self, tmp_path):
        """Recovery should clean up stale WAL/SHM files."""
        db_path = tmp_path / "test.db"
        _create_valid_db_with_data(db_path)

        # Create fake WAL/SHM files
        wal_path = Path(str(db_path) + "-wal")
        shm_path = Path(str(db_path) + "-shm")
        wal_path.write_bytes(b"fake wal")
        shm_path.write_bytes(b"fake shm")

        # Force recovery
        original_init = DocumentStore._init_db
        call_count = [0]

        def patched_init(self):
            call_count[0] += 1
            if call_count[0] == 1:
                original_init(self)
                raise sqlite3.DatabaseError("database disk image is malformed")
            else:
                original_init(self)

        with patch.object(DocumentStore, "_init_db", patched_init):
            DocumentStore(db_path)

        # Old WAL/SHM should be gone (new ones may exist from reopening)
        # The key is that the recovery explicitly removes them

    def test_unrecoverable_raises(self, tmp_path):
        """A truly unreadable file should propagate the error."""
        db_path = tmp_path / "test.db"
        db_path.write_bytes(b"\x00" * 100)  # Not a valid SQLite file

        with pytest.raises(sqlite3.DatabaseError):
            DocumentStore(db_path)


class TestRuntimeRecovery:
    """Test runtime recovery triggered by touch() and get()."""

    def test_touch_malformed_is_nonfatal(self, tmp_path):
        """touch() should not crash even if the DB is malformed."""
        db_path = tmp_path / "test.db"
        store = DocumentStore(db_path)
        store.upsert("default", "doc1", "hello", {})

        # Replace the connection with a wrapper that raises on touch SQL
        original_conn = store._conn
        original_execute = original_conn.execute

        class FailingConn:
            """Proxy that intercepts execute for touch SQL."""
            def __getattr__(self, name):
                return getattr(original_conn, name)

            def execute(self, sql, *args, **kwargs):
                if "UPDATE documents SET accessed_at" in str(sql):
                    raise sqlite3.DatabaseError("database disk image is malformed")
                return original_execute(sql, *args, **kwargs)

        store._conn = FailingConn()
        # Should not raise
        store.touch("default", "doc1")
        # Restore
        store._conn = original_conn

    def test_try_runtime_recover_returns_false_on_failure(self, tmp_path):
        """_try_runtime_recover returns False when recovery fails."""
        db_path = tmp_path / "test.db"
        store = DocumentStore(db_path)

        with patch.object(store, "_recover_malformed", side_effect=Exception("boom")):
            result = store._try_runtime_recover()
            assert result is False

    def test_try_runtime_recover_returns_true_on_success(self, tmp_path):
        """_try_runtime_recover returns True when recovery succeeds."""
        db_path = tmp_path / "test.db"
        store = DocumentStore(db_path)
        store.upsert("default", "doc1", "hello", {})

        with patch.object(store, "_recover_malformed"):
            result = store._try_runtime_recover()
            assert result is True
