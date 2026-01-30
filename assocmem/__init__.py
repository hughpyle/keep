"""
Associative Memory

A persistent associative store with semantic similarity search, full-text search,
and tag-based retrieval.

Quick Start:
    from assocmem import AssociativeMemory
    
    mem = AssociativeMemory()  # uses .assocmem/ at git repo root
    mem.update("file:///path/to/document.md", source_tags={"project": "myproject"})
    results = mem.find("something similar to this query")

CLI Usage:
    python -m assocmem find "query text"
    python -m assocmem update file:///path/to/doc.md -t category=docs
    python -m assocmem collections --json

Default Store:
    .assocmem/ at the git repository root (created automatically).
    Override with ASSOCMEM_STORE_PATH or explicit path argument.

Environment Variables:
    ASSOCMEM_STORE_PATH      - Override default store location
    ASSOCMEM_OPENAI_API_KEY  - API key for OpenAI providers

The store is initialized automatically on first use. Configuration is persisted
in a TOML file within the store directory.
"""

from .api import AssociativeMemory
from .types import Item, filter_non_system_tags, SYSTEM_TAG_PREFIX
from .context import WorkingContext, TopicSummary, RoutingContext

__version__ = "0.1.0"
__all__ = [
    "AssociativeMemory", 
    "Item", 
    "WorkingContext", 
    "TopicSummary", 
    "RoutingContext",
    "filter_non_system_tags",
    "SYSTEM_TAG_PREFIX",
]
