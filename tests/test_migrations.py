"""Schema migration tests for DocumentStore.

Verifies all migration paths: v0→v3, v1→v3, v2→v3, and the v3 no-op fast path.
Each test constructs a database at a specific schema version using raw SQL,
then opens it via DocumentStore and verifies the migration result.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from keep.api import Keeper
from keep.document_store import DocumentStore, PartInfo, SCHEMA_VERSION
from keep.types import utc_now


def _create_v0_db(path: Path, docs: list[tuple] = None):
    """Create a v0 database (documents table only, no user_version)."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE documents (
            id TEXT NOT NULL,
            collection TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (id, collection)
        )
    """)
    if docs:
        for doc in docs:
            conn.execute(
                "INSERT INTO documents (id, collection, summary, tags_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                doc,
            )
    # user_version stays at 0 (default)
    conn.commit()
    conn.close()


def _create_v1_db(path: Path, docs: list[tuple] = None):
    """Create a v1 database (has document_versions table)."""
    _create_v0_db(path, docs)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE document_versions (
            id TEXT NOT NULL,
            collection TEXT NOT NULL,
            version INTEGER NOT NULL,
            summary TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            content_hash TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, collection, version)
        )
    """)
    conn.execute("""
        CREATE INDEX idx_versions_doc
        ON document_versions(id, collection, version DESC)
    """)
    # Add content_hash column (was added before v1 in the code)
    conn.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def _create_v2_db(path: Path, docs: list[tuple] = None):
    """Create a v2 database (has accessed_at column)."""
    _create_v1_db(path, docs)
    conn = sqlite3.connect(str(path))
    conn.execute("ALTER TABLE documents ADD COLUMN accessed_at TEXT")
    conn.execute(
        "UPDATE documents SET accessed_at = updated_at WHERE accessed_at IS NULL"
    )
    conn.execute("""
        CREATE INDEX idx_documents_accessed ON documents(accessed_at)
    """)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()


def _create_v13_parts_db(
    path: Path,
    *,
    summary: str,
    content: str,
    tags: dict[str, object] | None = None,
) -> None:
    """Create a v13 database with the legacy document_parts.content column."""
    db = DocumentStore(path)
    try:
        db.upsert("default", "doc1", "Doc 1", {})
        db.upsert_parts(
            "default",
            "doc1",
            [PartInfo(1, summary, tags or {}, utc_now())],
        )
    finally:
        db.close()

    conn = sqlite3.connect(str(path))
    conn.execute(
        "ALTER TABLE document_parts ADD COLUMN content TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        """
        UPDATE document_parts
        SET content = ?
        WHERE id = 'doc1' AND collection = 'default' AND part_num = 1
        """,
        (content,),
    )
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()


def _get_columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _get_tables(conn) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _get_indexes(conn, table: str) -> set[str]:
    return {
        r[1] for r in conn.execute(f"PRAGMA index_list({table})").fetchall()
        if r[1]  # skip autoindex
    }


class TestMigrationV0ToV3:
    """Fresh v0 database → should migrate to v3 with all features."""

    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "test.db"
        _create_v0_db(path, [
            ("doc1", "default", "hello", json.dumps({"bundled_hash": "a" * 64}),
             "2025-01-01", "2025-01-01"),
            ("doc2", "default", "world", "{}", "2025-01-02", "2025-01-02"),
        ])
        return DocumentStore(path)

    def test_schema_version_updated(self, db):
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_versions_table_created(self, db):
        assert "document_versions" in _get_tables(db._conn)

    def test_accessed_at_column_added(self, db):
        assert "accessed_at" in _get_columns(db._conn, "documents")

    def test_content_hash_column_exists(self, db):
        assert "content_hash" in _get_columns(db._conn, "documents")

    def test_bundled_hash_truncated(self, db):
        row = db._conn.execute(
            "SELECT tags_json FROM documents WHERE id = 'doc1'"
        ).fetchone()
        tags = json.loads(row[0])
        assert len(tags["bundled_hash"]) == 10

    def test_indexes_created(self, db):
        indexes = _get_indexes(db._conn, "documents")
        assert "idx_documents_collection" in indexes
        assert "idx_documents_updated" in indexes
        assert "idx_documents_accessed" in indexes

    def test_data_preserved(self, db):
        rec = db.get("default", "doc1")
        assert rec is not None
        assert rec.summary == "hello"
        rec2 = db.get("default", "doc2")
        assert rec2 is not None
        assert rec2.summary == "world"


class TestMigrationV1ToV3:
    """v1 database (has versions table) → v3."""

    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "test.db"
        _create_v1_db(path, [
            ("doc1", "default", "hello", "{}", "2025-01-01", "2025-01-01"),
        ])
        return DocumentStore(path)

    def test_schema_version_updated(self, db):
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_accessed_at_added(self, db):
        assert "accessed_at" in _get_columns(db._conn, "documents")

    def test_versions_table_preserved(self, db):
        assert "document_versions" in _get_tables(db._conn)

    def test_data_preserved(self, db):
        rec = db.get("default", "doc1")
        assert rec is not None
        assert rec.summary == "hello"


class TestMigrationV2ToV3:
    """v2 database (has accessed_at) → v3 with hash truncation."""

    @pytest.fixture
    def db(self, tmp_path):
        path = tmp_path / "test.db"
        _create_v2_db(path, [
            ("doc1", "default", "hello", json.dumps({"bundled_hash": "x" * 64}),
             "2025-01-01", "2025-01-01"),
        ])
        # Add a long content_hash
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE documents SET content_hash = ? WHERE id = 'doc1'",
            ("y" * 64,),
        )
        conn.commit()
        conn.close()
        return DocumentStore(path)

    def test_schema_version_updated(self, db):
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_content_hash_truncated(self, db):
        row = db._conn.execute(
            "SELECT content_hash FROM documents WHERE id = 'doc1'"
        ).fetchone()
        assert len(row[0]) == 10

    def test_bundled_hash_truncated(self, db):
        row = db._conn.execute(
            "SELECT tags_json FROM documents WHERE id = 'doc1'"
        ).fetchone()
        tags = json.loads(row[0])
        assert len(tags["bundled_hash"]) == 10

    def test_indexes_created(self, db):
        indexes = _get_indexes(db._conn, "documents")
        assert "idx_documents_collection" in indexes
        assert "idx_documents_updated" in indexes


class TestMigrationAlreadyCurrent:
    """v3 database — no migration should run, no writes on reopen."""

    def test_no_writes_on_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"

        # Create and fully migrate
        ds1 = DocumentStore(db_path)
        ds1.upsert("default", "x", "hello", {})
        ds1._conn.close()

        # Checkpoint to flush WAL
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        wal_path = str(db_path) + "-wal"
        wal_before = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

        # Reopen — should skip migration
        ds2 = DocumentStore(db_path)
        wal_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        ds2._conn.close()

        assert wal_after == wal_before, (
            f"Unexpected writes: WAL grew from {wal_before} to {wal_after}"
        )


class TestMigrationV13ToV14:
    """Legacy part-content stores migrate to summary-only parts."""

    def test_divergent_content_promotes_to_summary_and_queues_reindex(self, tmp_path):
        db_path = tmp_path / "v13-divergent.db"
        _create_v13_parts_db(
            db_path,
            summary="Short prefix",
            content="Full migrated part text",
            tags={"topic": "pdf"},
        )

        db = DocumentStore(db_path)
        try:
            assert db._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert "content" not in _get_columns(db._conn, "document_parts")

            part = db.get_part("default", "doc1", 1)
            assert part is not None
            assert part.summary == "Full migrated part text"
            assert db.migrated_parts_for_reindex == [{
                "id": "doc1",
                "collection": "default",
                "part_num": 1,
                "summary": "Full migrated part text",
                "tags": {"topic": "pdf"},
            }]

            hits = db.query_fts("default", "migrated", limit=10)
            assert any(hit[0] == "doc1@p1" for hit in hits)
        finally:
            db.close()

    def test_empty_legacy_content_preserves_summary_without_reindex(self, tmp_path):
        db_path = tmp_path / "v13-empty-content.db"
        _create_v13_parts_db(
            db_path,
            summary="Canonical summary text",
            content="",
            tags={"topic": "note"},
        )

        db = DocumentStore(db_path)
        try:
            part = db.get_part("default", "doc1", 1)
            assert part is not None
            assert part.summary == "Canonical summary text"
            assert db.migrated_parts_for_reindex == []

            hits = db.query_fts("default", "canonical", limit=10)
            assert any(hit[0] == "doc1@p1" for hit in hits)
        finally:
            db.close()

    def test_both_empty_rows_migrate_without_crashing(self, tmp_path):
        db_path = tmp_path / "v13-both-empty.db"
        _create_v13_parts_db(db_path, summary="", content="")

        db = DocumentStore(db_path)
        try:
            part = db.get_part("default", "doc1", 1)
            assert part is not None
            assert part.summary == ""
            assert db.migrated_parts_for_reindex == []
        finally:
            db.close()

    def test_reopen_after_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "v13-idempotent.db"
        _create_v13_parts_db(
            db_path,
            summary="Short prefix",
            content="Full migrated part text",
        )

        db1 = DocumentStore(db_path)
        try:
            assert len(db1.migrated_parts_for_reindex) == 1
        finally:
            db1.close()

        db2 = DocumentStore(db_path)
        try:
            assert db2.migrated_parts_for_reindex == []
            part = db2.get_part("default", "doc1", 1)
            assert part is not None
            assert part.summary == "Full migrated part text"
            assert "content" not in _get_columns(db2._conn, "document_parts")
        finally:
            db2.close()


def test_enqueue_migrated_part_reindex_clears_pending_migration_list(
    mock_providers, tmp_path,
):
    kp = Keeper(store_path=tmp_path)
    try:
        kp._document_store.migrated_parts_for_reindex = [{
            "id": "doc1",
            "collection": "default",
            "part_num": 1,
            "summary": "Updated part text",
            "tags": {"topic": "x"},
        }]
        before = kp._pending_queue.count()

        queued = kp._enqueue_migrated_part_reindex()

        assert queued == 1
        assert kp._document_store.migrated_parts_for_reindex == []
        assert kp._pending_queue.count() == before + 1
    finally:
        kp.close()
