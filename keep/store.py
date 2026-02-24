"""
Vector store implementation using ChromaDb.

This is the first concrete store implementation. The interface is designed
to be extractable to a Protocol when additional backends are needed.

For now, ChromaDb is the only implementation — and that's fine.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .model_lock import ModelLock
from .types import Item, utc_now

logger = logging.getLogger(__name__)


@dataclass
class StoreResult:
    """Result from a store query with raw data before Item conversion."""
    id: str
    summary: str
    tags: dict[str, str]
    distance: float | None = None  # Lower is more similar in Chroma
    
    def to_item(self) -> Item:
        """Convert to Item, transforming distance to similarity score."""
        # Chroma cosine distance is in [0, 2]; convert to 0-1 similarity
        # score = 1 - distance/2 gives 1.0 for identical, 0.0 for orthogonal
        score = None
        if self.distance is not None:
            score = 1.0 - self.distance / 2.0
        return Item(id=self.id, summary=self.summary, tags=self.tags, score=score)


class ChromaStore:
    """
    Persistent vector store using ChromaDb.
    
    Each collection maps to a ChromaDb collection. Items are stored with:
    - id: The item's URI or custom identifier
    - embedding: Vector representation for similarity search
    - document: The item's summary (stored for retrieval, searchable)
    - metadata: All tags (flattened to strings for Chroma compatibility)
    
    The store is initialized at a specific path and persists across sessions.
    
    Future: This class's public interface could become a Protocol for
    pluggable backends (SQLite+faiss, Postgres+pgvector, etc.)
    """
    
    def __init__(self, store_path: Path, embedding_dimension: Optional[int] = None):
        """
        Initialize or open a ChromaDb store.

        Args:
            store_path: Directory for persistent storage
            embedding_dimension: Expected dimension of embeddings (for validation).
                Can be None for read-only access; will be set on first write.
        """
        import chromadb
        from chromadb.config import Settings

        self._store_path = store_path
        self._embedding_dimension = embedding_dimension

        # Ensure store directory exists
        store_path.mkdir(parents=True, exist_ok=True)

        # Disable Posthog telemetry completely.  ChromaDB sets posthog.disabled
        # but the default client is created with send=posthog.send (default True),
        # which starts a background consumer thread and registers an atexit handler.
        # That consumer can deadlock on shutdown in lock.acquire().
        try:
            import posthog
            posthog.send = False
            posthog.disabled = True
        except ImportError:
            pass

        # Initialize persistent client
        self._client = chromadb.PersistentClient(
            path=str(store_path / "chroma"),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            )
        )
        
        # Cache of collection handles
        self._collections: dict[str, Any] = {}
        # Set during L2→cosine migration so caller can trigger reindex
        self.migrated_to_cosine = False

        # Cross-process write serialization (fcntl file lock)
        self._chroma_lock = ModelLock(store_path / ".chroma.lock")
        # Epoch sentinel for staleness detection
        self._epoch_path = store_path / ".chroma.epoch"
        self._last_epoch: float = self._read_epoch()
        # Re-entrancy guard: True when write lock is already held
        self._lock_held = False
    
    @property
    def embedding_dimension(self) -> Optional[int]:
        """Current expected embedding dimension (may be None before first write)."""
        return self._embedding_dimension

    def reset_embedding_dimension(self, dimension: int) -> None:
        """Update expected embedding dimension (for provider changes)."""
        self._embedding_dimension = dimension

    # -------------------------------------------------------------------------
    # Cross-process coordination
    # -------------------------------------------------------------------------

    def _read_epoch(self) -> float:
        """Read the sentinel file's mtime, or 0.0 if it doesn't exist yet."""
        try:
            return self._epoch_path.stat().st_mtime
        except (FileNotFoundError, OSError):
            return 0.0

    def _bump_epoch(self) -> None:
        """Touch sentinel file and cache the new mtime.

        Called after every ChromaDB write while the write lock is held.
        Other processes detect the mtime change and reload their client.
        """
        self._epoch_path.touch()
        self._last_epoch = self._epoch_path.stat().st_mtime

    def _check_freshness(self) -> None:
        """If another process has written since our last observation, reload.

        Compares the sentinel file mtime to our cached value. A mismatch
        means another process bumped the epoch, so our in-memory hnswlib
        index is stale. Recreating the PersistentClient forces a fresh
        load from disk.
        """
        if self._client is None:
            return  # Store is closed
        current_epoch = self._read_epoch()
        if current_epoch != self._last_epoch:
            self._reload_client()
            self._last_epoch = current_epoch

    def _reload_client(self) -> None:
        """Recreate PersistentClient to pick up on-disk changes.

        Clears the collection handle cache and creates a fresh client,
        which forces ChromaDB to reload the hnswlib index from disk.
        """
        import chromadb
        from chromadb.config import Settings

        logger.debug("Reloading ChromaDB client (epoch changed)")
        self._collections.clear()
        self._client = chromadb.PersistentClient(
            path=str(self._store_path / "chroma"),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )

    # -------------------------------------------------------------------------
    # Collection management helpers
    # -------------------------------------------------------------------------

    def _get_collection(self, name: str) -> Any:
        """Get or create a collection by name, migrating L2→cosine if needed."""
        if name not in self._collections:
            coll = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
            # Migrate: if existing collection uses L2, recreate with cosine.
            # This is a write operation — acquire lock if not already held.
            if coll.metadata.get("hnsw:space") != "cosine":
                acquired = False
                if not self._lock_held:
                    self._chroma_lock.acquire()
                    acquired = True
                    self._lock_held = True
                try:
                    logger.info(
                        "Migrating collection %s from %s to cosine (requires reindex)",
                        name, coll.metadata.get("hnsw:space"),
                    )
                    self._client.delete_collection(name)
                    coll = self._client.create_collection(
                        name=name,
                        metadata={"hnsw:space": "cosine"},
                    )
                    self.migrated_to_cosine = True
                    self._bump_epoch()
                finally:
                    if acquired:
                        self._lock_held = False
                        self._chroma_lock.release()
            self._collections[name] = coll
        return self._collections[name]
    
    def _tags_to_metadata(self, tags: dict[str, str]) -> dict[str, Any]:
        """
        Convert tags to Chroma metadata format.
        
        Chroma metadata values must be str, int, float, or bool.
        We store everything as strings for consistency.
        """
        return {k: str(v) for k, v in tags.items()}
    
    def _metadata_to_tags(self, metadata: dict[str, Any] | None) -> dict[str, str]:
        """Convert Chroma metadata back to tags."""
        if metadata is None:
            return {}
        return {k: str(v) for k, v in metadata.items()}
    
    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------
    
    def upsert(
        self,
        collection: str,
        id: str,
        embedding: list[float],
        summary: str,
        tags: dict[str, str],
    ) -> None:
        """
        Insert or update an item in the store.

        Args:
            collection: Collection name
            id: Item identifier (URI or custom)
            embedding: Vector embedding
            summary: Human-readable summary (stored as document)
            tags: All tags (source + system + generated)
        """
        # Validate or set embedding dimension
        if self._embedding_dimension is None:
            self._embedding_dimension = len(embedding)
        elif len(embedding) != self._embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._embedding_dimension}, "
                f"got {len(embedding)}"
            )

        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Add timestamp if not present
                now = utc_now()
                if "_updated" not in tags:
                    tags = {**tags, "_updated": now}
                if "_created" not in tags:
                    # Check if item exists to preserve original created time
                    existing = coll.get(ids=[id], include=["metadatas"])
                    if existing["ids"]:
                        old_created = existing["metadatas"][0].get("_created")
                        if old_created:
                            tags = {**tags, "_created": old_created}
                        else:
                            tags = {**tags, "_created": now}
                    else:
                        tags = {**tags, "_created": now}

                # Add date portion for easier date queries
                tags = {**tags, "_updated_date": now[:10]}

                coll.upsert(
                    ids=[id],
                    embeddings=[embedding],
                    documents=[summary],
                    metadatas=[self._tags_to_metadata(tags)],
                )
                self._bump_epoch()
            finally:
                self._lock_held = False

    def upsert_version(
        self,
        collection: str,
        id: str,
        version: int,
        embedding: list[float],
        summary: str,
        tags: dict[str, str],
    ) -> None:
        """
        Store an archived version with a versioned ID.

        The versioned ID format is: {id}@v{version}
        Metadata includes _version and _base_id for filtering/navigation.

        Args:
            collection: Collection name
            id: Base item identifier (not versioned)
            version: Version number (1=oldest archived)
            embedding: Vector embedding
            summary: Human-readable summary
            tags: All tags from the archived version
        """
        if self._embedding_dimension is None:
            self._embedding_dimension = len(embedding)
        elif len(embedding) != self._embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._embedding_dimension}, "
                f"got {len(embedding)}"
            )

        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Versioned ID format
                versioned_id = f"{id}@v{version}"

                # Add version metadata
                versioned_tags = dict(tags)
                versioned_tags["_version"] = str(version)
                versioned_tags["_base_id"] = id

                coll.upsert(
                    ids=[versioned_id],
                    embeddings=[embedding],
                    documents=[summary],
                    metadatas=[self._tags_to_metadata(versioned_tags)],
                )
                self._bump_epoch()
            finally:
                self._lock_held = False

    def upsert_part(
        self,
        collection: str,
        id: str,
        part_num: int,
        embedding: list[float],
        summary: str,
        tags: dict[str, str],
    ) -> None:
        """
        Store a document part with a part-numbered ID.

        The part ID format is: {id}@p{part_num}
        Metadata includes _part_num and _base_id for filtering/navigation.

        Args:
            collection: Collection name
            id: Base item identifier (not part-numbered)
            part_num: Part number (1-indexed)
            embedding: Vector embedding
            summary: Human-readable summary of the part
            tags: All tags for this part
        """
        if self._embedding_dimension is None:
            self._embedding_dimension = len(embedding)
        elif len(embedding) != self._embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self._embedding_dimension}, "
                f"got {len(embedding)}"
            )

        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Part ID format
                part_id = f"{id}@p{part_num}"

                # Add part metadata
                part_tags = dict(tags)
                part_tags["_part_num"] = str(part_num)
                part_tags["_base_id"] = id

                coll.upsert(
                    ids=[part_id],
                    embeddings=[embedding],
                    documents=[summary],
                    metadatas=[self._tags_to_metadata(part_tags)],
                )
                self._bump_epoch()
            finally:
                self._lock_held = False

    def delete_parts(self, collection: str, id: str) -> int:
        """
        Delete all parts for a document from the vector store.

        Args:
            collection: Collection name
            id: Base item identifier

        Returns:
            Number of parts deleted
        """
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)
                try:
                    parts = coll.get(
                        where={"_base_id": id},
                        include=[],
                    )
                    part_ids = [pid for pid in parts["ids"] if "@p" in pid]
                    if part_ids:
                        coll.delete(ids=part_ids)
                    count = len(part_ids)
                except ValueError:
                    count = 0  # No parts exist
                self._bump_epoch()
                return count
            finally:
                self._lock_held = False

    def delete(self, collection: str, id: str, delete_versions: bool = True) -> bool:
        """
        Delete an item from the store.

        Args:
            collection: Collection name
            id: Item identifier
            delete_versions: If True, also delete versioned copies ({id}@v{N})

        Returns:
            True if item existed and was deleted, False if not found
        """
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Check existence first
                existing = coll.get(ids=[id])
                if not existing["ids"]:
                    return False

                coll.delete(ids=[id])

                if delete_versions:
                    # Delete all versioned copies and parts
                    # Query by _base_id metadata to find all versions (@v{N}) and parts (@p{N})
                    try:
                        related = coll.get(
                            where={"_base_id": id},
                            include=[],
                        )
                        if related["ids"]:
                            coll.delete(ids=related["ids"])
                    except ValueError:
                        pass  # Metadata filter may fail if no related entries exist

                self._bump_epoch()
                return True
            finally:
                self._lock_held = False

    def update_summary(self, collection: str, id: str, summary: str) -> bool:
        """
        Update just the summary of an existing item.

        Used by lazy summarization to replace placeholder summaries
        with real generated summaries.

        Args:
            collection: Collection name
            id: Item identifier
            summary: New summary text

        Returns:
            True if item was updated, False if not found
        """
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Get existing item — include embeddings so we can preserve them.
                # ChromaDB re-embeds when documents= is passed, using its default
                # embedding function (384d all-MiniLM-L6-v2) which would corrupt
                # embeddings produced by the configured provider (e.g. 768d nomic).
                existing = coll.get(ids=[id], include=["metadatas", "embeddings"])
                if not existing["ids"]:
                    return False

                # Update metadata with new timestamp
                metadata = existing["metadatas"][0] or {}
                now = utc_now()
                metadata["_updated"] = now
                metadata["_updated_date"] = now[:10]

                # Update document text + metadata, passing existing embeddings
                # to prevent ChromaDB from re-embedding with its default function.
                coll.update(
                    ids=[id],
                    documents=[summary],
                    embeddings=[existing["embeddings"][0]],
                    metadatas=[metadata],
                )
                self._bump_epoch()
                return True
            finally:
                self._lock_held = False

    def update_tags(self, collection: str, id: str, tags: dict[str, str]) -> bool:
        """
        Update tags of an existing item without changing embedding or summary.

        Args:
            collection: Collection name
            id: Item identifier
            tags: New tags dict (replaces existing)

        Returns:
            True if item was updated, False if not found
        """
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)

                # Get existing item
                existing = coll.get(ids=[id], include=["metadatas"])
                if not existing["ids"]:
                    return False

                # Update timestamp
                now = utc_now()
                tags = dict(tags)  # Copy to avoid mutating input
                tags["_updated"] = now
                tags["_updated_date"] = now[:10]

                # Convert to metadata format
                metadata = self._tags_to_metadata(tags)

                coll.update(
                    ids=[id],
                    metadatas=[metadata],
                )
                self._bump_epoch()
                return True
            finally:
                self._lock_held = False

    # -------------------------------------------------------------------------
    # Read Operations
    # -------------------------------------------------------------------------
    
    def get(self, collection: str, id: str) -> StoreResult | None:
        """
        Retrieve a specific item by ID.
        
        Args:
            collection: Collection name
            id: Item identifier
            
        Returns:
            StoreResult if found, None otherwise
        """
        self._check_freshness()
        coll = self._get_collection(collection)
        result = coll.get(
            ids=[id],
            include=["documents", "metadatas"],
        )

        if not result["ids"]:
            return None

        return StoreResult(
            id=result["ids"][0],
            summary=result["documents"][0] or "",
            tags=self._metadata_to_tags(result["metadatas"][0]),
        )

    def exists(self, collection: str, id: str) -> bool:
        """Check if an item exists in the store."""
        self._check_freshness()
        coll = self._get_collection(collection)
        result = coll.get(ids=[id], include=[])
        return bool(result["ids"])

    def get_embedding(self, collection: str, id: str) -> list[float] | None:
        """
        Retrieve the stored embedding for a document.

        Args:
            collection: Collection name
            id: Item identifier

        Returns:
            Embedding vector if found, None otherwise
        """
        self._check_freshness()
        coll = self._get_collection(collection)
        result = coll.get(ids=[id], include=["embeddings"])
        if not result["ids"] or result["embeddings"] is None or len(result["embeddings"]) == 0:
            return None
        return list(result["embeddings"][0])

    _LIST_IDS_PAGE = 5000

    def list_ids(self, collection: str) -> list[str]:
        """
        List all document IDs in a collection.

        Paginates internally to avoid loading all IDs in a single
        ChromaDB call, which can be slow or OOM on large collections.

        Args:
            collection: Collection name

        Returns:
            List of document IDs
        """
        self._check_freshness()
        coll = self._get_collection(collection)
        all_ids: list[str] = []
        offset = 0
        while True:
            result = coll.get(include=[], limit=self._LIST_IDS_PAGE, offset=offset)
            batch = result["ids"]
            if not batch:
                break
            all_ids.extend(batch)
            if len(batch) < self._LIST_IDS_PAGE:
                break
            offset += len(batch)
        return all_ids

    def find_missing_ids(self, collection: str, ids: list[str]) -> set[str]:
        """
        Given a list of IDs, return those not present in ChromaDB.

        Checks in batches to avoid oversized requests.

        Args:
            collection: Collection name
            ids: IDs to check

        Returns:
            Set of IDs not found in ChromaDB
        """
        self._check_freshness()
        coll = self._get_collection(collection)
        missing: set[str] = set()
        batch_size = self._LIST_IDS_PAGE
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i + batch_size]
            result = coll.get(ids=batch, include=[])
            found = set(result["ids"])
            missing.update(id for id in batch if id not in found)
        return missing

    def query_embedding(
        self,
        collection: str,
        embedding: list[float],
        limit: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[StoreResult]:
        """
        Query by embedding similarity.
        
        Args:
            collection: Collection name
            embedding: Query embedding vector
            limit: Maximum results to return
            where: Optional metadata filter (Chroma where clause)
            
        Returns:
            List of results ordered by similarity (most similar first)
        """
        self._check_freshness()
        coll = self._get_collection(collection)
        
        query_params = {
            "query_embeddings": [embedding],
            "n_results": limit,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_params["where"] = where
        
        result = coll.query(**query_params)
        
        results = []
        for i, id in enumerate(result["ids"][0]):
            results.append(StoreResult(
                id=id,
                summary=result["documents"][0][i] or "",
                tags=self._metadata_to_tags(result["metadatas"][0][i]),
                distance=result["distances"][0][i] if result["distances"] else None,
            ))
        
        return results
    
    def query_metadata(
        self,
        collection: str,
        where: dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoreResult]:
        """
        Query by metadata filter (tag query).

        Args:
            collection: Collection name
            where: Chroma where clause for metadata filtering
            limit: Maximum results to return
            offset: Number of results to skip (for pagination)

        Returns:
            List of matching results (no particular order)
        """
        self._check_freshness()
        coll = self._get_collection(collection)

        result = coll.get(
            where=where,
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"],
        )
        
        results = []
        for i, id in enumerate(result["ids"]):
            results.append(StoreResult(
                id=id,
                summary=result["documents"][i] or "",
                tags=self._metadata_to_tags(result["metadatas"][i]),
            ))
        
        return results
    
    def query_fulltext(
        self,
        collection: str,
        query: str,
        limit: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[StoreResult]:
        """
        Query by full-text search on document content (summaries).

        Args:
            collection: Collection name
            query: Text to search for
            limit: Maximum results to return
            where: Optional metadata filter (Chroma where clause)

        Returns:
            List of matching results
        """
        self._check_freshness()
        coll = self._get_collection(collection)

        # Chroma's where_document does substring matching
        get_params = {
            "where_document": {"$contains": query},
            "limit": limit,
            "include": ["documents", "metadatas"],
        }
        if where:
            get_params["where"] = where

        result = coll.get(**get_params)

        results = []
        for i, id in enumerate(result["ids"]):
            results.append(StoreResult(
                id=id,
                summary=result["documents"][i] or "",
                tags=self._metadata_to_tags(result["metadatas"][i]),
            ))

        return results
    
    # -------------------------------------------------------------------------
    # Collection Management
    # -------------------------------------------------------------------------
    
    def list_collections(self) -> list[str]:
        """List all collection names in the store."""
        self._check_freshness()
        collections = self._client.list_collections()
        return [c.name for c in collections]

    def delete_collection(self, name: str) -> bool:
        """
        Delete an entire collection.

        Args:
            name: Collection name

        Returns:
            True if collection existed and was deleted
        """
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                try:
                    self._client.delete_collection(name)
                    self._collections.pop(name, None)
                    self._bump_epoch()
                    return True
                except ValueError:
                    return False
            finally:
                self._lock_held = False

    def count(self, collection: str) -> int:
        """Return the number of items in a collection."""
        self._check_freshness()
        coll = self._get_collection(collection)
        return coll.count()

    # -------------------------------------------------------------------------
    # Batch Operations
    # -------------------------------------------------------------------------

    def get_entries_full(
        self, collection: str, ids: list[str]
    ) -> list[dict[str, Any]]:
        """
        Batch get entries with embeddings, summaries, and metadata.

        Returns list of dicts with keys: id, embedding, summary, tags.
        Only entries that exist are returned (missing IDs are silently skipped).
        """
        if not ids:
            return []
        self._check_freshness()
        coll = self._get_collection(collection)
        result = coll.get(
            ids=ids,
            include=["embeddings", "documents", "metadatas"],
        )
        entries = []
        if result["ids"]:
            for i, entry_id in enumerate(result["ids"]):
                embedding = None
                if result["embeddings"] is not None and i < len(result["embeddings"]):
                    emb = result["embeddings"][i]
                    embedding = list(emb) if emb is not None else None
                summary = ""
                if result["documents"] is not None and i < len(result["documents"]):
                    summary = result["documents"][i] or ""
                tags = {}
                if result["metadatas"] is not None and i < len(result["metadatas"]):
                    tags = self._metadata_to_tags(result["metadatas"][i])
                entries.append({
                    "id": entry_id,
                    "embedding": embedding,
                    "summary": summary,
                    "tags": tags,
                })
        return entries

    def upsert_batch(
        self,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        summaries: list[str],
        tags: list[dict[str, str]],
    ) -> None:
        """
        Batch upsert entries with embeddings.

        All lists must have the same length. Tags are converted to store
        metadata format internally.
        """
        if not ids:
            return
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)
                metadatas = [self._tags_to_metadata(t) for t in tags]
                coll.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=summaries,
                    metadatas=metadatas,
                )
                self._bump_epoch()
            finally:
                self._lock_held = False

    def delete_entries(self, collection: str, ids: list[str]) -> None:
        """
        Delete specific entries by ID.

        Unlike delete(), this does not expand version IDs — it deletes
        exactly the IDs given. Silently ignores IDs that don't exist.
        """
        if not ids:
            return
        with self._chroma_lock:
            self._lock_held = True
            try:
                self._check_freshness()
                coll = self._get_collection(collection)
                try:
                    coll.delete(ids=ids)
                except ValueError:
                    pass  # Some IDs may not exist
                self._bump_epoch()
            finally:
                self._lock_held = False

    # -------------------------------------------------------------------------
    # Resource Management
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """
        Close ChromaDB client and release resources.

        Good practice to call when done, though Python's GC will clean up eventually.
        """
        self._collections.clear()
        # ChromaDB PersistentClient doesn't have explicit close(),
        # but clearing references allows garbage collection
        self._client = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close resources."""
        self.close()
        return False

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass  # Suppress errors during garbage collection
