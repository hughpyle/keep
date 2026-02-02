"""
Document store using SQLite.

Stores canonical document records separate from embeddings.
This enables multiple embedding providers to index the same documents.

The document store is the source of truth for:
- Document identity (URI / custom ID)
- Summary text
- Tags (source + system)
- Timestamps

Embeddings are stored in ChromaDB collections, keyed by embedding provider.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class DocumentRecord:
    """
    A canonical document record.
    
    This is the source of truth, independent of any embedding index.
    """
    id: str
    collection: str
    summary: str
    tags: dict[str, str]
    created_at: str
    updated_at: str


class DocumentStore:
    """
    SQLite-backed store for canonical document records.
    
    Separates document metadata from embedding storage, enabling:
    - Multiple embedding providers per document
    - Efficient tag/metadata queries without ChromaDB
    - Clear separation of concerns
    """
    
    def __init__(self, store_path: Path):
        """
        Args:
            store_path: Path to SQLite database file
        """
        self._db_path = store_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT NOT NULL,
                collection TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (id, collection)
            )
        """)
        
        # Index for collection queries
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_collection
            ON documents(collection)
        """)
        
        # Index for timestamp queries
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_updated
            ON documents(updated_at)
        """)
        
        self._conn.commit()
    
    def _now(self) -> str:
        """Current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()
    
    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------
    
    def upsert(
        self,
        collection: str,
        id: str,
        summary: str,
        tags: dict[str, str],
    ) -> DocumentRecord:
        """
        Insert or update a document record.
        
        Preserves created_at on update. Updates updated_at always.
        
        Args:
            collection: Collection name
            id: Document identifier (URI or custom)
            summary: Document summary text
            tags: All tags (source + system)
            
        Returns:
            The stored DocumentRecord
        """
        now = self._now()
        tags_json = json.dumps(tags, ensure_ascii=False)
        
        # Check if exists to preserve created_at
        existing = self.get(collection, id)
        created_at = existing.created_at if existing else now
        
        self._conn.execute("""
            INSERT OR REPLACE INTO documents
            (id, collection, summary, tags_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (id, collection, summary, tags_json, created_at, now))
        self._conn.commit()
        
        return DocumentRecord(
            id=id,
            collection=collection,
            summary=summary,
            tags=tags,
            created_at=created_at,
            updated_at=now,
        )
    
    def update_summary(self, collection: str, id: str, summary: str) -> bool:
        """
        Update just the summary of an existing document.
        
        Used by lazy summarization to replace placeholder summaries.
        
        Args:
            collection: Collection name
            id: Document identifier
            summary: New summary text
            
        Returns:
            True if document was found and updated, False otherwise
        """
        now = self._now()
        
        cursor = self._conn.execute("""
            UPDATE documents
            SET summary = ?, updated_at = ?
            WHERE id = ? AND collection = ?
        """, (summary, now, id, collection))
        self._conn.commit()
        
        return cursor.rowcount > 0
    
    def update_tags(
        self,
        collection: str,
        id: str,
        tags: dict[str, str],
    ) -> bool:
        """
        Update tags of an existing document.
        
        Args:
            collection: Collection name
            id: Document identifier
            tags: New tags dict (replaces existing)
            
        Returns:
            True if document was found and updated, False otherwise
        """
        now = self._now()
        tags_json = json.dumps(tags, ensure_ascii=False)
        
        cursor = self._conn.execute("""
            UPDATE documents
            SET tags_json = ?, updated_at = ?
            WHERE id = ? AND collection = ?
        """, (tags_json, now, id, collection))
        self._conn.commit()
        
        return cursor.rowcount > 0
    
    def delete(self, collection: str, id: str) -> bool:
        """
        Delete a document record.
        
        Args:
            collection: Collection name
            id: Document identifier
            
        Returns:
            True if document existed and was deleted
        """
        cursor = self._conn.execute("""
            DELETE FROM documents
            WHERE id = ? AND collection = ?
        """, (id, collection))
        self._conn.commit()
        
        return cursor.rowcount > 0
    
    # -------------------------------------------------------------------------
    # Read Operations
    # -------------------------------------------------------------------------
    
    def get(self, collection: str, id: str) -> Optional[DocumentRecord]:
        """
        Get a document by ID.
        
        Args:
            collection: Collection name
            id: Document identifier
            
        Returns:
            DocumentRecord if found, None otherwise
        """
        cursor = self._conn.execute("""
            SELECT id, collection, summary, tags_json, created_at, updated_at
            FROM documents
            WHERE id = ? AND collection = ?
        """, (id, collection))
        
        row = cursor.fetchone()
        if row is None:
            return None
        
        return DocumentRecord(
            id=row["id"],
            collection=row["collection"],
            summary=row["summary"],
            tags=json.loads(row["tags_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    
    def get_many(
        self,
        collection: str,
        ids: list[str],
    ) -> dict[str, DocumentRecord]:
        """
        Get multiple documents by ID.
        
        Args:
            collection: Collection name
            ids: List of document identifiers
            
        Returns:
            Dict mapping id → DocumentRecord (missing IDs omitted)
        """
        if not ids:
            return {}
        
        placeholders = ",".join("?" * len(ids))
        cursor = self._conn.execute(f"""
            SELECT id, collection, summary, tags_json, created_at, updated_at
            FROM documents
            WHERE collection = ? AND id IN ({placeholders})
        """, (collection, *ids))
        
        results = {}
        for row in cursor:
            results[row["id"]] = DocumentRecord(
                id=row["id"],
                collection=row["collection"],
                summary=row["summary"],
                tags=json.loads(row["tags_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        
        return results
    
    def exists(self, collection: str, id: str) -> bool:
        """Check if a document exists."""
        cursor = self._conn.execute("""
            SELECT 1 FROM documents
            WHERE id = ? AND collection = ?
        """, (id, collection))
        return cursor.fetchone() is not None
    
    def list_ids(
        self,
        collection: str,
        limit: Optional[int] = None,
    ) -> list[str]:
        """
        List document IDs in a collection.
        
        Args:
            collection: Collection name
            limit: Maximum number to return (None for all)
            
        Returns:
            List of document IDs
        """
        if limit:
            cursor = self._conn.execute("""
                SELECT id FROM documents
                WHERE collection = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (collection, limit))
        else:
            cursor = self._conn.execute("""
                SELECT id FROM documents
                WHERE collection = ?
                ORDER BY updated_at DESC
            """, (collection,))
        
        return [row["id"] for row in cursor]
    
    def count(self, collection: str) -> int:
        """Count documents in a collection."""
        cursor = self._conn.execute("""
            SELECT COUNT(*) FROM documents
            WHERE collection = ?
        """, (collection,))
        return cursor.fetchone()[0]
    
    def count_all(self) -> int:
        """Count total documents across all collections."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM documents")
        return cursor.fetchone()[0]

    # -------------------------------------------------------------------------
    # Tag Queries
    # -------------------------------------------------------------------------

    def list_distinct_tag_keys(self, collection: str) -> list[str]:
        """
        List all distinct tag keys used in the collection.

        Excludes system tags (prefixed with _).

        Returns:
            Sorted list of distinct tag keys
        """
        cursor = self._conn.execute("""
            SELECT tags_json FROM documents
            WHERE collection = ?
        """, (collection,))

        keys: set[str] = set()
        for row in cursor:
            tags = json.loads(row["tags_json"])
            for key in tags:
                if not key.startswith("_"):
                    keys.add(key)

        return sorted(keys)

    def list_distinct_tag_values(self, collection: str, key: str) -> list[str]:
        """
        List all distinct values for a given tag key.

        Args:
            collection: Collection name
            key: Tag key to get values for

        Returns:
            Sorted list of distinct values
        """
        cursor = self._conn.execute("""
            SELECT tags_json FROM documents
            WHERE collection = ?
        """, (collection,))

        values: set[str] = set()
        for row in cursor:
            tags = json.loads(row["tags_json"])
            if key in tags:
                values.add(tags[key])

        return sorted(values)

    def query_by_tag_key(
        self,
        collection: str,
        key: str,
        limit: int = 100,
    ) -> list[DocumentRecord]:
        """
        Find documents that have a specific tag key (any value).

        Args:
            collection: Collection name
            key: Tag key to search for
            limit: Maximum results

        Returns:
            List of matching DocumentRecords
        """
        # SQLite JSON functions for tag key existence
        # json_extract returns NULL if key doesn't exist
        cursor = self._conn.execute("""
            SELECT id, collection, summary, tags_json, created_at, updated_at
            FROM documents
            WHERE collection = ?
              AND json_extract(tags_json, ?) IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT ?
        """, (collection, f"$.{key}", limit))

        results = []
        for row in cursor:
            results.append(DocumentRecord(
                id=row["id"],
                collection=row["collection"],
                summary=row["summary"],
                tags=json.loads(row["tags_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            ))

        return results

    # -------------------------------------------------------------------------
    # Collection Management
    # -------------------------------------------------------------------------
    
    def list_collections(self) -> list[str]:
        """List all collection names."""
        cursor = self._conn.execute("""
            SELECT DISTINCT collection FROM documents
            ORDER BY collection
        """)
        return [row["collection"] for row in cursor]
    
    def delete_collection(self, collection: str) -> int:
        """
        Delete all documents in a collection.
        
        Args:
            collection: Collection name
            
        Returns:
            Number of documents deleted
        """
        cursor = self._conn.execute("""
            DELETE FROM documents
            WHERE collection = ?
        """, (collection,))
        self._conn.commit()
        return cursor.rowcount
    
    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------
    
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
        self.close()
