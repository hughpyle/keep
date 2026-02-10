"""
Shared pytest fixtures for keep tests.

Provides mock providers to avoid loading heavy ML models during testing.
"""

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class MockEmbeddingProvider:
    """
    Deterministic mock embedding provider for testing.

    Generates consistent embeddings based on text hash - no ML model loading.
    """

    dimension = 384
    model_name = "mock-model"

    def __init__(self):
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text: str) -> list[float]:
        """Generate deterministic embedding from text hash."""
        self.embed_calls += 1
        h = hashlib.md5(text.encode()).hexdigest()
        # Create 384-dim vector from hash (deterministic)
        embedding = []
        for i in range(0, 32, 2):
            val = int(h[i:i+2], 16) / 255.0
            embedding.append(val)
        # Pad to full dimension
        embedding = (embedding * 24)[:self.dimension]
        return embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        self.batch_calls += 1
        return [self.embed(t) for t in texts]


class MockSummarizationProvider:
    """Mock summarization provider - returns truncated content."""

    def summarize(self, content: str) -> str:
        """Return first 200 chars as summary."""
        return content[:200] if len(content) > 200 else content


class MockDocumentProvider:
    """Mock document provider for URI fetching."""

    def fetch(self, uri: str) -> Any:
        """Return mock document."""
        mock_doc = MagicMock()
        mock_doc.content = f"Content for {uri}"
        mock_doc.content_type = "text/plain"
        return mock_doc


@pytest.fixture
def mock_embedding_provider():
    """Create a fresh MockEmbeddingProvider instance."""
    return MockEmbeddingProvider()


class MockChromaStore:
    """Mock ChromaStore to avoid loading real ChromaDB."""

    def __init__(self, store_path: Path, embedding_dimension: int = None):
        self._store_path = store_path
        self._embedding_dimension = embedding_dimension or 384
        self._data: dict[str, dict] = {}  # collection -> {id -> record}

    def upsert(self, collection: str, id: str, embedding: list[float],
               summary: str, tags: dict[str, str]) -> None:
        if collection not in self._data:
            self._data[collection] = {}
        self._data[collection][id] = {
            "summary": summary,
            "tags": tags,
            "embedding": embedding,
        }

    def get(self, collection: str, id: str):
        from keep.store import StoreResult
        if collection not in self._data or id not in self._data[collection]:
            return None
        rec = self._data[collection][id]
        return StoreResult(id=id, summary=rec["summary"], tags=rec["tags"])

    def exists(self, collection: str, id: str) -> bool:
        return collection in self._data and id in self._data[collection]

    def delete(self, collection: str, id: str, delete_versions: bool = True) -> bool:
        if collection in self._data and id in self._data[collection]:
            del self._data[collection][id]
            return True
        return False

    def query_embedding(self, collection: str, embedding: list[float],
                       limit: int = 10, where: dict = None) -> list:
        from keep.store import StoreResult
        if collection not in self._data:
            return []
        results = []
        for id, rec in list(self._data[collection].items())[:limit]:
            results.append(StoreResult(
                id=id, summary=rec["summary"], tags=rec["tags"], distance=0.1
            ))
        return results

    def query_metadata(self, collection: str, where: dict, limit: int = 100) -> list:
        from keep.store import StoreResult
        if collection not in self._data:
            return []
        results = []
        for id, rec in list(self._data[collection].items())[:limit]:
            # Simple filter: check if all where conditions match
            match = all(rec["tags"].get(k) == v for k, v in where.items())
            if match:
                results.append(StoreResult(id=id, summary=rec["summary"], tags=rec["tags"]))
        return results

    def query_fulltext(self, collection: str, query: str, limit: int = 10,
                      where: dict = None) -> list:
        from keep.store import StoreResult
        if collection not in self._data:
            return []
        results = []
        for id, rec in self._data[collection].items():
            if query.lower() in rec["summary"].lower():
                results.append(StoreResult(id=id, summary=rec["summary"], tags=rec["tags"]))
        return results[:limit]

    def list_collections(self) -> list[str]:
        return list(self._data.keys()) or ["default"]

    def count(self, collection: str) -> int:
        return len(self._data.get(collection, {}))

    def get_embedding(self, collection: str, id: str) -> list[float] | None:
        if collection in self._data and id in self._data[collection]:
            return self._data[collection][id].get("embedding")
        return None

    def list_ids(self, collection: str) -> list[str]:
        return list(self._data.get(collection, {}).keys())

    def find_missing_ids(self, collection: str, ids: list[str]) -> set[str]:
        existing = set(self._data.get(collection, {}).keys())
        return {id for id in ids if id not in existing}

    def update_summary(self, collection: str, id: str, summary: str) -> bool:
        if collection in self._data and id in self._data[collection]:
            self._data[collection][id]["summary"] = summary
            return True
        return False

    def update_tags(self, collection: str, id: str, tags: dict) -> bool:
        if collection in self._data and id in self._data[collection]:
            self._data[collection][id]["tags"] = tags
            return True
        return False

    def close(self) -> None:
        self._data.clear()


class MockDocumentStore:
    """Mock DocumentStore to avoid loading real SQLite."""

    def __init__(self, db_path: Path):
        self._data: dict[str, dict] = {}  # collection -> {id -> record}

    def upsert(self, collection: str, id: str, summary: str, tags: dict,
               content_hash: str = None) -> tuple["DocumentRecord", bool]:
        if collection not in self._data:
            self._data[collection] = {}
        existed = id in self._data[collection]
        content_changed = (
            existed
            and content_hash is not None
            and self._data[collection][id].get("content_hash") != content_hash
        )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        created_at = self._data[collection].get(id, {}).get("created_at", now)
        self._data[collection][id] = {
            "summary": summary,
            "tags": tags,
            "content_hash": content_hash,
            "created_at": created_at,
            "updated_at": now,
        }
        # Return a record-like object matching DocumentStore.upsert() signature
        class Record:
            pass
        r = Record()
        r.id = id
        r.collection = collection
        r.summary = summary
        r.tags = tags
        r.content_hash = content_hash
        r.created_at = created_at
        r.updated_at = now
        return (r, content_changed)

    def get(self, collection: str, id: str):
        if collection not in self._data or id not in self._data[collection]:
            return None
        rec = self._data[collection][id]
        # Return a simple object with the expected attributes
        class Record:
            pass
        r = Record()
        r.id = id
        r.summary = rec["summary"]
        r.tags = rec["tags"]
        r.content_hash = rec.get("content_hash")
        r.created_at = rec["created_at"]
        r.updated_at = rec["updated_at"]
        return r

    def exists(self, collection: str, id: str) -> bool:
        return collection in self._data and id in self._data[collection]

    def delete(self, collection: str, id: str, delete_versions: bool = True) -> bool:
        if collection in self._data and id in self._data[collection]:
            del self._data[collection][id]
            return True
        return False

    def list_ids(self, collection: str, limit: int = None) -> list[str]:
        ids = list(self._data.get(collection, {}).keys())
        return ids[:limit] if limit else ids

    def count(self, collection: str = None) -> int:
        if collection:
            return len(self._data.get(collection, {}))
        return sum(len(c) for c in self._data.values())

    def version_count(self, collection: str, id: str) -> int:
        return 0

    def update_tags(self, collection: str, id: str, tags: dict) -> bool:
        if collection in self._data and id in self._data[collection]:
            self._data[collection][id]["tags"] = tags
            return True
        return False

    def update_summary(self, collection: str, id: str, summary: str) -> bool:
        if collection in self._data and id in self._data[collection]:
            self._data[collection][id]["summary"] = summary
            return True
        return False

    def list_recent(self, collection: str, limit: int = 10, order_by: str = "updated") -> list:
        if collection not in self._data:
            return []
        records = []
        for id, rec in list(self._data[collection].items())[:limit]:
            class Record:
                pass
            r = Record()
            r.id = id
            r.collection = collection
            r.summary = rec["summary"]
            r.tags = rec["tags"]
            r.content_hash = rec.get("content_hash")
            r.created_at = rec["created_at"]
            r.updated_at = rec["updated_at"]
            records.append(r)
        return records

    def query_by_tag_key(self, collection: str, key: str, limit: int = 100,
                         since_date: str = None) -> list:
        if collection not in self._data:
            return []
        results = []
        for id, rec in self._data[collection].items():
            if key in rec["tags"]:
                class Record:
                    pass
                r = Record()
                r.id = id
                r.collection = collection
                r.summary = rec["summary"]
                r.tags = rec["tags"]
                r.content_hash = rec.get("content_hash")
                r.created_at = rec["created_at"]
                r.updated_at = rec["updated_at"]
                results.append(r)
        return results[:limit]

    def list_distinct_tag_keys(self, collection: str) -> list[str]:
        keys = set()
        for rec in self._data.get(collection, {}).values():
            for k in rec["tags"]:
                if not k.startswith("_"):
                    keys.add(k)
        return sorted(keys)

    def list_distinct_tag_values(self, collection: str, key: str) -> list[str]:
        values = set()
        for rec in self._data.get(collection, {}).values():
            if key in rec["tags"]:
                values.add(rec["tags"][key])
        return sorted(values)

    def get_version(self, collection: str, id: str, offset: int = 0):
        return None

    def list_versions(self, collection: str, id: str, limit: int = 10) -> list:
        return []

    def get_version_nav(self, collection: str, id: str,
                        current_version=None, limit: int = 3) -> dict:
        return {"prev": []}

    def restore_latest_version(self, collection: str, id: str):
        return None

    def list_collections(self) -> list[str]:
        return list(self._data.keys())

    def touch(self, collection: str, id: str) -> None:
        pass

    def touch_many(self, collection: str, ids: list[str]) -> None:
        pass

    def close(self) -> None:
        self._data.clear()


class MockPendingSummaryQueue:
    """Mock pending summary queue."""

    def __init__(self, db_path: Path):
        self._queue = []

    def enqueue(self, id: str, collection: str, content: str) -> None:
        """Add item to pending queue."""
        self._queue.append({"id": id, "collection": collection, "content": content})

    def dequeue(self, limit: int = 10) -> list:
        """Get items from queue."""
        return []

    def complete(self, id: str, collection: str) -> None:
        """Mark item as complete."""
        pass

    def fail(self, id: str, collection: str) -> None:
        """Mark item as failed."""
        pass

    def count(self) -> int:
        """Count pending items."""
        return len(self._queue)

    def close(self) -> None:
        self._queue.clear()


@pytest.fixture
def mock_providers():
    """
    Fixture that patches all providers AND stores to use mocks.

    This avoids loading:
    - Real ML models (sentence-transformers, etc.)
    - Real ChromaDB
    - Real SQLite document store

    Use this fixture for tests that create Keeper instances but don't
    need real ML model or database behavior.

    Usage:
        def test_something(mock_providers, tmp_path):
            kp = Keeper(store_path=tmp_path)
            # ... test using mocked providers and stores
    """
    mock_embed = MockEmbeddingProvider()
    mock_summ = MockSummarizationProvider()
    mock_doc = MockDocumentProvider()

    mock_reg = MagicMock()
    mock_reg.create_document.return_value = mock_doc
    mock_reg.create_embedding.return_value = mock_embed
    mock_reg.create_summarization.return_value = mock_summ

    with patch("keep.api.get_registry", return_value=mock_reg), \
         patch("keep.api.CachingEmbeddingProvider", side_effect=lambda p, **kw: p), \
         patch("keep.api.ChromaStore", MockChromaStore), \
         patch("keep.document_store.DocumentStore", MockDocumentStore), \
         patch("keep.api.PendingSummaryQueue", MockPendingSummaryQueue):
        yield {
            "embedding": mock_embed,
            "summarization": mock_summ,
            "document": mock_doc,
            "registry": mock_reg,
        }


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (loading real ML models)"
    )
    config.addinivalue_line(
        "markers", "e2e: marks tests as end-to-end (require real providers)"
    )
