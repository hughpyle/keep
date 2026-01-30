"""
Core API for associative memory.

This is the minimal working implementation focused on:
- update(): fetch → embed → summarize → store
- remember(): embed → summarize → store  
- find(): embed query → search
- get(): retrieve by ID
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import load_or_create_config, StoreConfig
from .paths import get_default_store_path
from .providers import get_registry
from .providers.base import (
    DocumentProvider,
    EmbeddingProvider,
    SummarizationProvider,
)
from .providers.embedding_cache import CachingEmbeddingProvider
from .store import ChromaStore
from .types import Item, filter_non_system_tags


# Collection name validation: lowercase ASCII and underscores only
COLLECTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class AssociativeMemory:
    """
    Persistent associative memory with semantic search capabilities.
    
    Example:
        mem = AssociativeMemory()
        mem.update("file:///path/to/readme.md")
        results = mem.find("installation instructions")
    """
    
    def __init__(
        self,
        store_path: Optional[str | Path] = None,
        collection: str = "default",
        decay_half_life_days: float = 30.0
    ) -> None:
        """
        Initialize or open an existing associative memory store.
        
        Args:
            store_path: Path to store directory. Uses default if not specified.
            collection: Default collection name.
            decay_half_life_days: Memory decay half-life in days (ACT-R model).
                After this many days, an item's effective relevance is halved.
                Set to 0 or negative to disable decay.
        """
        # Resolve store path
        if store_path is None:
            self._store_path = get_default_store_path()
        else:
            self._store_path = Path(store_path).resolve()
        
        # Validate collection name
        if not COLLECTION_NAME_PATTERN.match(collection):
            raise ValueError(
                f"Invalid collection name '{collection}'. "
                "Must be lowercase ASCII, starting with a letter."
            )
        self._default_collection = collection
        self._decay_half_life_days = decay_half_life_days
        
        # Load or create configuration
        self._config: StoreConfig = load_or_create_config(self._store_path)
        
        # Initialize providers
        registry = get_registry()
        
        self._document_provider: DocumentProvider = registry.create_document(
            self._config.document.name,
            self._config.document.params,
        )
        
        # Create embedding provider with caching
        base_embedding_provider = registry.create_embedding(
            self._config.embedding.name,
            self._config.embedding.params,
        )
        cache_path = self._store_path / "embedding_cache.db"
        self._embedding_provider: EmbeddingProvider = CachingEmbeddingProvider(
            base_embedding_provider,
            cache_path=cache_path,
        )
        
        self._summarization_provider: SummarizationProvider = registry.create_summarization(
            self._config.summarization.name,
            self._config.summarization.params,
        )
        
        # Initialize store
        self._store = ChromaStore(
            self._store_path,
            embedding_dimension=self._embedding_provider.dimension,
        )
    
    def _resolve_collection(self, collection: Optional[str]) -> str:
        """Resolve collection name, validating if provided."""
        if collection is None:
            return self._default_collection
        if not COLLECTION_NAME_PATTERN.match(collection):
            raise ValueError(f"Invalid collection name: {collection}")
        return collection
    
    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------
    
    def update(
        self,
        id: str,
        source_tags: Optional[dict[str, str]] = None,
        *,
        collection: Optional[str] = None
    ) -> Item:
        """
        Insert or update a document in the store.
        
        Fetches the document, generates embeddings and summary, then stores it.
        """
        coll = self._resolve_collection(collection)
        
        # Fetch document
        doc = self._document_provider.fetch(id)
        
        # Generate embedding
        embedding = self._embedding_provider.embed(doc.content)
        
        # Generate summary
        summary = self._summarization_provider.summarize(doc.content)
        
        # Build tags
        tags = {}
        
        # Add source tags (filtered to prevent system tag override)
        if source_tags:
            tags.update(filter_non_system_tags(source_tags))
        
        # Add system tags
        tags["_source"] = "uri"
        if doc.content_type:
            tags["_mime_type"] = doc.content_type
        
        # Store
        self._store.upsert(
            collection=coll,
            id=id,
            embedding=embedding,
            summary=summary,
            tags=tags,
        )
        
        # Return the stored item
        result = self._store.get(coll, id)
        return result.to_item()
    
    def remember(
        self,
        content: str,
        *,
        id: Optional[str] = None,
        source_tags: Optional[dict[str, str]] = None,
        collection: Optional[str] = None
    ) -> Item:
        """
        Store inline content directly (without fetching from a URI).
        
        Use for conversation snippets, notes, insights.
        """
        coll = self._resolve_collection(collection)
        
        # Generate ID if not provided
        if id is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
            id = f"mem:{timestamp}"
        
        # Generate embedding
        embedding = self._embedding_provider.embed(content)
        
        # Generate summary
        summary = self._summarization_provider.summarize(content)
        
        # Build tags
        tags = {}
        
        # Add source tags (filtered)
        if source_tags:
            tags.update(filter_non_system_tags(source_tags))
        
        # Add system tags
        tags["_source"] = "inline"
        
        # Store
        self._store.upsert(
            collection=coll,
            id=id,
            embedding=embedding,
            summary=summary,
            tags=tags,
        )
        
        # Return the stored item
        result = self._store.get(coll, id)
        return result.to_item()
    
    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------
    
    def _apply_recency_decay(self, items: list[Item]) -> list[Item]:
        """
        Apply ACT-R style recency decay to search results.
        
        Multiplies each item's similarity score by a decay factor based on
        time since last update. Uses exponential decay with configurable half-life.
        
        Formula: effective_score = similarity × 0.5^(days_elapsed / half_life)
        """
        if self._decay_half_life_days <= 0:
            return items  # Decay disabled
        
        now = datetime.now(timezone.utc)
        decayed_items = []
        
        for item in items:
            # Get last update time from tags
            updated_str = item.tags.get("_updated")
            if updated_str and item.score is not None:
                try:
                    # Parse ISO timestamp
                    updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                    days_elapsed = (now - updated).total_seconds() / 86400
                    
                    # Exponential decay: 0.5^(days/half_life)
                    decay_factor = 0.5 ** (days_elapsed / self._decay_half_life_days)
                    decayed_score = item.score * decay_factor
                    
                    # Create new Item with decayed score
                    decayed_items.append(Item(
                        id=item.id,
                        summary=item.summary,
                        tags=item.tags,
                        score=decayed_score
                    ))
                except (ValueError, TypeError):
                    # If timestamp parsing fails, keep original
                    decayed_items.append(item)
            else:
                decayed_items.append(item)
        
        # Re-sort by decayed score (highest first)
        decayed_items.sort(key=lambda x: x.score if x.score is not None else 0, reverse=True)
        
        return decayed_items
    
    def find(
        self,
        query: str,
        *,
        limit: int = 10,
        collection: Optional[str] = None
    ) -> list[Item]:
        """
        Find items using semantic similarity search.
        
        Scores are adjusted by recency decay (ACT-R model) - older items
        have reduced effective relevance unless recently accessed.
        """
        coll = self._resolve_collection(collection)
        
        # Embed query
        embedding = self._embedding_provider.embed(query)
        
        # Search (fetch extra to account for re-ranking)
        fetch_limit = limit * 2 if self._decay_half_life_days > 0 else limit
        results = self._store.query_embedding(coll, embedding, limit=fetch_limit)
        
        # Convert to Items and apply decay
        items = [r.to_item() for r in results]
        items = self._apply_recency_decay(items)
        
        return items[:limit]
    
    def find_similar(
        self,
        id: str,
        *,
        limit: int = 10,
        include_self: bool = False,
        collection: Optional[str] = None
    ) -> list[Item]:
        """
        Find items similar to an existing item.
        """
        coll = self._resolve_collection(collection)
        
        # Get the item to find its embedding
        item = self._store.get(coll, id)
        if item is None:
            raise KeyError(f"Item not found: {id}")
        
        # Search using the summary's embedding
        embedding = self._embedding_provider.embed(item.summary)
        actual_limit = limit + 1 if not include_self else limit
        results = self._store.query_embedding(coll, embedding, limit=actual_limit)
        
        # Filter self if needed
        if not include_self:
            results = [r for r in results if r.id != id]
        
        # Convert to Items and apply decay
        items = [r.to_item() for r in results]
        items = self._apply_recency_decay(items)
        
        return items[:limit]
    
    def query_fulltext(
        self,
        query: str,
        *,
        limit: int = 10,
        collection: Optional[str] = None
    ) -> list[Item]:
        """
        Search item summaries using full-text search.
        """
        coll = self._resolve_collection(collection)
        results = self._store.query_fulltext(coll, query, limit=limit)
        return [r.to_item() for r in results]
    
    def query_tag(
        self,
        limit: int = 100,
        collection: Optional[str] = None,
        **tags: str
    ) -> list[Item]:
        """
        Find items by tag(s).
        
        Usage: 
            query_tag(tradition="buddhist")
            query_tag(tradition="buddhist", source="mn22")
        """
        coll = self._resolve_collection(collection)
        
        if not tags:
            raise ValueError("At least one tag must be specified")
        
        # Build where clause for multiple tags
        where = {k: v for k, v in tags.items()}
        
        results = self._store.query_metadata(coll, where, limit=limit)
        return [r.to_item() for r in results]
    
    # -------------------------------------------------------------------------
    # Direct Access
    # -------------------------------------------------------------------------
    
    def get(self, id: str, *, collection: Optional[str] = None) -> Optional[Item]:
        """
        Retrieve a specific item by ID.
        """
        coll = self._resolve_collection(collection)
        result = self._store.get(coll, id)
        if result is None:
            return None
        return result.to_item()
    
    def exists(self, id: str, *, collection: Optional[str] = None) -> bool:
        """
        Check if an item exists in the store.
        """
        coll = self._resolve_collection(collection)
        return self._store.exists(coll, id)
    
    def delete(self, id: str, *, collection: Optional[str] = None) -> bool:
        """
        Delete an item from the store.
        
        Returns True if item existed and was deleted.
        """
        coll = self._resolve_collection(collection)
        return self._store.delete(coll, id)
    
    # -------------------------------------------------------------------------
    # Collection Management
    # -------------------------------------------------------------------------
    
    def list_collections(self) -> list[str]:
        """
        List all collections in the store.
        """
        return self._store.list_collections()
    
    def count(self, *, collection: Optional[str] = None) -> int:
        """
        Count items in a collection.
        """
        coll = self._resolve_collection(collection)
        return self._store.count(coll)
    
    def embedding_cache_stats(self) -> dict:
        """
        Get embedding cache statistics.
        
        Returns dict with: entries, hits, misses, hit_rate, cache_path
        """
        if isinstance(self._embedding_provider, CachingEmbeddingProvider):
            return self._embedding_provider.stats()
        return {"enabled": False}
