"""
Pluggable storage backend factory.

Creates storage backends (DocumentStore, VectorStore, PendingQueue) based on
configuration. Local backends use SQLite + ChromaDB. External backends register
via the ``keep.backends`` entry point group.

External backend packages provide a factory function::

    def create_stores(config: StoreConfig) -> StoreBundle:
        ...

and register it in their pyproject.toml::

    [project.entry-points."keep.backends"]
    my-backend = "my_package.backend:create_stores"
"""

from typing import NamedTuple, Optional

from .config import StoreConfig
from .protocol import DocumentStoreProtocol, PendingQueueProtocol, VectorStoreProtocol


class StoreBundle(NamedTuple):
    """Collection of storage backends returned by the factory."""
    doc_store: DocumentStoreProtocol
    vector_store: VectorStoreProtocol
    pending_queue: PendingQueueProtocol
    is_local: bool  # True for filesystem-backed stores


class NullPendingQueue:
    """No-op pending queue for backends that handle summarization server-side."""

    def enqueue(self, id: str, collection: str, content: str) -> None:
        pass

    def dequeue(self, limit: int = 10) -> list:
        return []

    def complete(self, id: str, collection: str) -> None:
        pass

    def count(self) -> int:
        return 0

    def stats(self) -> dict:
        return {"pending": 0, "collections": 0, "max_attempts": 0, "oldest": None}

    def clear(self) -> int:
        return 0

    def close(self) -> None:
        pass


def create_stores(config: StoreConfig) -> StoreBundle:
    """
    Create storage backends from configuration.

    For ``backend = "local"`` (default), creates SQLite DocumentStore,
    ChromaDB ChromaStore, and SQLite PendingSummaryQueue.

    For other values, loads the backend via the ``keep.backends`` entry
    point group.
    """
    if config.backend == "local":
        return _create_local_stores(config)
    return _load_backend(config.backend, config)


def _create_local_stores(config: StoreConfig) -> StoreBundle:
    """Create the default local storage backends."""
    from .document_store import DocumentStore
    from .pending_summaries import PendingSummaryQueue
    from .store import ChromaStore

    store_path = config.path

    pending_queue = PendingSummaryQueue(store_path / "pending_summaries.db")
    doc_store = DocumentStore(store_path / "documents.db")

    embedding_dim: Optional[int] = None
    if config.embedding_identity:
        embedding_dim = config.embedding_identity.dimension
    vector_store = ChromaStore(store_path, embedding_dimension=embedding_dim)

    return StoreBundle(
        doc_store=doc_store,
        vector_store=vector_store,
        pending_queue=pending_queue,
        is_local=True,
    )


def _load_backend(name: str, config: StoreConfig) -> StoreBundle:
    """Load a backend by entry point name."""
    from importlib.metadata import entry_points

    eps = entry_points(group="keep.backends")
    for ep in eps:
        if ep.name == name:
            factory = ep.load()
            return factory(config)

    available = [ep.name for ep in eps]
    if available:
        raise ValueError(
            f"Unknown backend: {name!r}. Available: {available}"
        )
    raise ValueError(
        f"Unknown backend: {name!r}. No backends registered. "
        f"Install a backend package (e.g. pip install keepnotes-postgres)."
    )
