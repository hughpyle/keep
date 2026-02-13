"""
Protocol definitions for Keeper and its storage backends.

Defines interface contracts at two levels:
- KeeperProtocol: the public API (CLI, RemoteKeeper)
- VectorStoreProtocol / DocumentStoreProtocol: internal storage backends
  (ChromaDB/SQLite locally, Postgres/pgvector in cloud)
"""

from typing import Any, Optional, Protocol, runtime_checkable

from .document_store import DocumentRecord, VersionInfo
from .store import StoreResult
from .types import Item


@runtime_checkable
class KeeperProtocol(Protocol):
    """
    The public interface for reflective memory operations.

    Implemented by:
    - Keeper (local SQLite + ChromaDB backend)
    - RemoteKeeper (HTTP client to keepnotes.ai API)
    """

    # -- Write operations --

    def update(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
        *,
        summary: Optional[str] = None,
    ) -> Item: ...

    def remember(
        self,
        content: str,
        *,
        id: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> Item: ...

    def set_now(
        self,
        content: str,
        *,
        tags: Optional[dict[str, str]] = None,
    ) -> Item: ...

    def tag(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
    ) -> Optional[Item]: ...

    def delete(
        self,
        id: str,
        *,
        delete_versions: bool = True,
    ) -> bool: ...

    def revert(self, id: str) -> Optional[Item]: ...

    def move(
        self,
        name: str,
        *,
        source_id: str = ...,
        tags: Optional[dict[str, str]] = None,
        only_current: bool = False,
    ) -> Item: ...

    # -- Query operations --

    def find(
        self,
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
    ) -> list[Item]: ...

    def find_similar(
        self,
        id: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
        include_self: bool = False,
    ) -> list[Item]: ...

    def get_similar_for_display(
        self,
        id: str,
        *,
        limit: int = 3,
    ) -> list[Item]: ...

    def query_fulltext(
        self,
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
    ) -> list[Item]: ...

    def query_tag(
        self,
        key: Optional[str] = None,
        value: Optional[str] = None,
        *,
        limit: int = 100,
        since: Optional[str] = None,
    ) -> list[Item]: ...

    def list_tags(
        self,
        key: Optional[str] = None,
    ) -> list[str]: ...

    def resolve_meta(
        self,
        item_id: str,
        *,
        limit_per_doc: int = 3,
    ) -> dict[str, list[Item]]: ...

    def list_recent(
        self,
        limit: int = 10,
        *,
        since: Optional[str] = None,
        order_by: str = "updated",
        include_history: bool = False,
    ) -> list[Item]: ...

    # -- Direct access --

    def get(self, id: str) -> Optional[Item]: ...

    def get_now(self) -> Item: ...

    def get_version(
        self,
        id: str,
        offset: int = 0,
    ) -> Optional[Item]: ...

    def list_versions(
        self,
        id: str,
        limit: int = 10,
    ) -> list[VersionInfo]: ...

    def get_version_nav(
        self,
        id: str,
        current_version: Optional[int] = None,
        limit: int = 3,
    ) -> dict: ...

    def get_version_offset(self, item: Item) -> int: ...

    def exists(self, id: str) -> bool: ...

    # -- Collection management --

    def list_collections(self) -> list[str]: ...

    def count(self) -> int: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Storage backend protocols — internal to Keeper
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """
    Abstract vector search backend.

    Implemented by:
    - ChromaStore (local ChromaDB)
    - Cloud implementations (pgvector, Qdrant, etc.)
    """

    # -- Embedding dimension --

    @property
    def embedding_dimension(self) -> Optional[int]: ...

    def reset_embedding_dimension(self, dimension: int) -> None: ...

    # -- Single-item operations --

    def upsert(
        self,
        collection: str,
        id: str,
        embedding: list[float],
        summary: str,
        tags: dict[str, str],
    ) -> None: ...

    def upsert_version(
        self,
        collection: str,
        id: str,
        version: int,
        embedding: list[float],
        summary: str,
        tags: dict[str, str],
    ) -> None: ...

    def get(self, collection: str, id: str) -> Optional[StoreResult]: ...

    def get_embedding(self, collection: str, id: str) -> Optional[list[float]]: ...

    def get_content_hash(self, collection: str, id: str) -> Optional[str]: ...

    def exists(self, collection: str, id: str) -> bool: ...

    def delete(
        self, collection: str, id: str, delete_versions: bool = True
    ) -> bool: ...

    def update_summary(self, collection: str, id: str, summary: str) -> bool: ...

    def update_tags(
        self, collection: str, id: str, tags: dict[str, str]
    ) -> bool: ...

    # -- Batch operations --

    def get_entries_full(
        self, collection: str, ids: list[str]
    ) -> list[dict[str, Any]]: ...

    def upsert_batch(
        self,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        summaries: list[str],
        tags: list[dict[str, str]],
    ) -> None: ...

    def delete_entries(self, collection: str, ids: list[str]) -> None: ...

    # -- Search --

    def query_embedding(
        self,
        collection: str,
        embedding: list[float],
        limit: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[StoreResult]: ...

    def query_metadata(
        self,
        collection: str,
        where: dict[str, Any],
        limit: int = 100,
    ) -> list[StoreResult]: ...

    def query_fulltext(
        self,
        collection: str,
        query: str,
        limit: int = 10,
        where: Optional[dict[str, Any]] = None,
    ) -> list[StoreResult]: ...

    # -- Collection management --

    def list_ids(self, collection: str) -> list[str]: ...

    def find_missing_ids(
        self, collection: str, ids: list[str]
    ) -> set[str]: ...

    def list_collections(self) -> list[str]: ...

    def delete_collection(self, name: str) -> bool: ...

    def count(self, collection: str) -> int: ...

    def close(self) -> None: ...


@runtime_checkable
class DocumentStoreProtocol(Protocol):
    """
    Abstract document metadata backend.

    Implemented by:
    - DocumentStore (local SQLite)
    - Cloud implementations (Postgres, etc.)
    """

    # -- Write --

    def upsert(
        self,
        collection: str,
        id: str,
        summary: str,
        tags: dict[str, str],
        content_hash: Optional[str] = None,
    ) -> tuple[DocumentRecord, bool]: ...

    def update_summary(
        self, collection: str, id: str, summary: str
    ) -> bool: ...

    def update_tags(
        self, collection: str, id: str, tags: dict[str, str]
    ) -> bool: ...

    def touch(self, collection: str, id: str) -> None: ...

    def touch_many(self, collection: str, ids: list[str]) -> None: ...

    def delete(
        self, collection: str, id: str, delete_versions: bool = True
    ) -> bool: ...

    def restore_latest_version(
        self, collection: str, id: str
    ) -> Optional[DocumentRecord]: ...

    def copy_record(
        self, collection: str, from_id: str, to_id: str
    ) -> Optional[DocumentRecord]: ...

    def count_versions_from(
        self, collection: str, id: str, from_version: int
    ) -> int: ...

    # -- Read --

    def get(self, collection: str, id: str) -> Optional[DocumentRecord]: ...

    def get_many(
        self, collection: str, ids: list[str]
    ) -> dict[str, DocumentRecord]: ...

    def exists(self, collection: str, id: str) -> bool: ...

    def get_version(
        self, collection: str, id: str, offset: int = 0
    ) -> Optional[VersionInfo]: ...

    def list_versions(
        self, collection: str, id: str, limit: Optional[int] = None
    ) -> list[VersionInfo]: ...

    def get_version_nav(
        self, collection: str, id: str, offset: int = 1
    ) -> dict: ...

    def version_count(self, collection: str, id: str) -> int: ...

    def max_version(self, collection: str, id: str) -> int: ...

    # -- Query --

    def list_ids(
        self, collection: str, limit: Optional[int] = None
    ) -> list[str]: ...

    def list_recent(
        self,
        collection: str,
        limit: int = 10,
        order_by: str = "updated",
    ) -> list[DocumentRecord]: ...

    def list_recent_with_history(
        self, collection: str, limit: int = 10
    ) -> list[DocumentRecord]: ...

    def count(self, collection: str) -> int: ...

    def count_all(self) -> int: ...

    def query_by_id_prefix(
        self, collection: str, prefix: str
    ) -> list[DocumentRecord]: ...

    def list_distinct_tag_keys(self, collection: str) -> list[str]: ...

    def list_distinct_tag_values(
        self, collection: str, key: str
    ) -> list[str]: ...

    def query_by_tag_key(
        self, collection: str, key: str
    ) -> list[DocumentRecord]: ...

    # -- Version extraction (for move) --

    def extract_versions(
        self,
        collection: str,
        source_id: str,
        target_id: str,
        tag_filter: Optional[dict[str, str]] = None,
        only_current: bool = False,
    ) -> tuple[list[VersionInfo], Optional[DocumentRecord], int]: ...

    # -- Collection management --

    def list_collections(self) -> list[str]: ...

    def delete_collection(self, collection: str) -> int: ...

    def close(self) -> None: ...


@runtime_checkable
class PendingQueueProtocol(Protocol):
    """
    Abstract pending summary queue.

    Implemented by:
    - PendingSummaryQueue (local SQLite)
    - NullPendingQueue (no-op for backends that handle summarization server-side)
    - Cloud implementations (Postgres-backed queue, etc.)
    """

    def enqueue(self, id: str, collection: str, content: str) -> None: ...

    def dequeue(self, limit: int = 10) -> list: ...

    def complete(self, id: str, collection: str) -> None: ...

    def count(self) -> int: ...

    def stats(self) -> dict: ...

    def clear(self) -> int: ...

    def close(self) -> None: ...
