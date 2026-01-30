"""
Provider interfaces for associative memory services.

Each provider type defines a protocol that concrete implementations must follow.
Providers are configured at store initialization and handle the heavy lifting of:
- Embedding generation (for semantic search)
- Summarization (for human-readable recall)
- Tagging (for structured navigation)
- Document fetching (for URI resolution)

Concrete providers are auto-registered when this module is imported.
"""

from .base import (
    Document,
    EmbeddingProvider,
    SummarizationProvider,
    TaggingProvider,
    DocumentProvider,
    ProviderRegistry,
    get_registry,
)

# Import concrete providers to trigger registration
from . import documents
from . import embeddings
from . import summarization  # Default summarizers
from . import llm
from . import mlx  # Only registers on Apple Silicon

__all__ = [
    # Protocols
    "EmbeddingProvider",
    "SummarizationProvider", 
    "TaggingProvider",
    "DocumentProvider",
    # Data types
    "Document",
    # Registry
    "ProviderRegistry",
    "get_registry",
]

