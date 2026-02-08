# Architecture Overview

## What is keep?

**keep** is a reflective memory system providing persistent storage with vector similarity search. It's designed as an agent skill for OpenClaw and other agentic environments, enabling agents to remember information across sessions over time.

Think of it as: **ChromaDB + embeddings + summarization + tagging** wrapped in a simple API.

Published by Hugh Pyle, "inguz ᛜ outcomes", under the MIT license.
Contributions are welcome; code is conversation, "right speech" is encouraged.

---

## Core Concept

Every stored item has:
- **ID**: URI or custom identifier
- **Summary**: Human-readable text (stored, searchable)
- **Embedding**: Vector representation (for semantic search)
- **Tags**: Key-value metadata (for filtering)
- **Timestamps**: Created/updated (auto-managed)
- **Version History**: Previous versions archived automatically on update

The original document content is **not stored** — only the summary and embedding.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  API Layer (api.py)                                         │
│  - Keeper class                                             │
│  - High-level operations: update(), remember(), find()      │
│  - Version management: get_version(), list_versions()       │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┼──────────┬──────────────┬───────────┐
        │          │          │              │           │
        ▼          ▼          ▼              ▼           ▼
   ┌────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐
   │Document│ │Embedding│ │Summary   │ │Vector   │ │Document │
   │Provider│ │Provider │ │Provider  │ │Store    │ │Store    │
   └────────┘ └─────────┘ └──────────┘ └─────────┘ └─────────┘
       │          │            │             │           │
   fetch()    embed()     summarize()   vectors/    summaries/
   from URI   text→vec   text→summary   search      versions
```

### Components

**[api.py](keep/api.py)** — Main facade
- `Keeper` class
- Coordinates providers and store
- Implements query operations with recency decay

**[store.py](keep/store.py)** — Vector persistence
- `ChromaStore` wraps ChromaDB
- Handles vector storage, similarity search, metadata queries
- Versioned embeddings: `{id}@v{version}` for history

**[document_store.py](keep/document_store.py)** — Document persistence
- `DocumentStore` wraps SQLite
- Stores summaries, tags, timestamps
- Version history: archives previous versions on update

**[providers/](keep/providers/)** — Pluggable services
- **Document**: Fetch content from URIs (file://, https://)
- **Embedding**: Generate vectors (sentence-transformers, OpenAI, Ollama, MLX)
- **Summarization**: Generate summaries (truncate, LLM-based)
- **Registry**: Factory for lazy-loading providers

**[config.py](keep/config.py)** — Configuration
- Detects available providers (platform, API keys, Ollama)
- Persists choices in `keep.toml`
- Auto-creates on first use

**[types.py](keep/types.py)** — Data model
- `Item`: Immutable result type
- System tag protection (prefix: `_`)

---

## Data Flow

### Indexing: update(uri) or remember(content)

```
URI or content
    │
    ▼
┌─────────────────┐
│ Fetch/Use input │ ← DocumentProvider (for URIs only)
└────────┬────────┘
         │ raw bytes
         ▼
┌─────────────────┐
│ Content Regular-│ ← Extract text from HTML/PDF
│ ization         │   (scripts/styles removed)
└────────┬────────┘
         │ clean text
    ┌────┴────┬─────────────┐
    │         │             │
    ▼         ▼             ▼
  embed()  summarize()   tags (from args)
    │         │             │
    └────┬────┴─────────────┘
         │
    ┌────┴────────────────┐
    │                     │
    ▼                     ▼
┌─────────────────┐  ┌─────────────────┐
│ DocumentStore   │  │ ChromaStore     │
│ upsert()        │  │ upsert()        │
│ - summary       │  │ - embedding     │
│ - tags          │  │ - summary       │
│ - timestamps    │  │ - tags          │
│ - archive prev  │  │ - version embed │
└─────────────────┘  └─────────────────┘
```

**Versioning on update:**
- DocumentStore archives current version before updating
- ChromaStore adds versioned embedding (`{id}@v{N}`) if content changed
- Same content (hash match) skips duplicate embedding

### Retrieval: find(query)

```
query text
    │
    ▼
  embed()  ← EmbeddingProvider
    │
    │ query vector
    ▼
┌───────────────────┐
│ ChromaStore       │
│ query_embedding() │ ← L2 distance search
└─────────┬─────────┘
          │
          ▼ results with distance scores
    ┌──────────────┐
    │ Apply decay  │ ← Recency weighting (ACT-R style)
    │ score × 0.5^(days/half_life)
    └──────┬───────┘
           │
           ▼
    list[Item] (sorted by effective score)
```

### Delete / Revert: delete(id) or revert(id)

```
delete(id)
    │
    ▼
  version_count(id)
    │
    ├── 0 versions → full delete from both stores
    │
    └── N versions → revert to previous
            │
            ├─ get archived embedding from ChromaDB (id@vN)
            ├─ restore_latest_version() in DocumentStore
            │    (promote latest version row to current, delete version row)
            ├─ upsert restored embedding as current in ChromaDB
            └─ delete versioned entry (id@vN) from ChromaDB
```

---

## Key Design Decisions

**1. Schema as Data**
- System configuration stored as documents in the store
- Enables agents to query and update behavior
- (Not yet implemented: routing, guidance documents)

**2. Lazy Provider Loading**
- Providers registered at first use, not import time
- Avoids crashes when optional dependencies missing
- Better error messages about what's needed

**3. Separation of Concerns**
- Store is provider-agnostic (only knows about vectors/metadata)
- Providers are store-agnostic (only know about text→vectors)
- Easy to swap implementations

**4. No Original Content Storage**
- Reduces storage size
- Forces meaningful summarization
- URIs can be re-fetched if needed

**5. Immutable Items**
- `Item` is frozen dataclass
- Updates via `update()` return new Item
- Prevents accidental mutation bugs

**6. System Tag Protection**
- Tags prefixed with `_` are system-managed
- Source tags filtered before storage
- Prevents user override of timestamps, etc.

**7. Document Versioning**
- All documents retain history automatically on update
- Previous versions archived in SQLite `document_versions` table
- Content-addressed IDs for text updates enable versioning via tag changes
- Embeddings stored for all versions (enables temporal search)
- No auto-pruning: history preserved indefinitely

**8. Version-Based Addressing**
- Versions addressed by offset from current: 0=current, 1=previous, 2=two ago
- CLI uses `@V{N}` syntax for shell composition: `keep get "doc:1@V{1}"`
- Display format (v0, v1, v2) matches retrieval offset (`-V 0`, `-V 1`, `-V 2`)
- Offset computation assumes `list_versions()` returns newest-first ordering
- Security: literal ID lookup before `@V{N}` parsing prevents confusion attacks

---

## Storage Layout

```
store_path/
├── keep.toml               # Provider configuration
├── chroma/                 # ChromaDB persistence (vectors + metadata)
│   └── [collection]/       # One collection = one namespace
│       ├── embeddings
│       ├── metadata
│       └── documents
├── document_store.db       # SQLite store (summaries, tags, versions)
│   ├── documents           # Current version of each document
│   └── document_versions   # Archived previous versions
└── embedding_cache.db      # SQLite cache for embeddings
```

---

## Provider Types

### Embedding Providers
Generate vector representations for semantic search.

- **voyage**: API-based, Anthropic's recommended partner (VOYAGE_API_KEY)
- **openai**: API-based, high quality (OPENAI_API_KEY)
- **gemini**: API-based, Google (GEMINI_API_KEY)
- **ollama**: Local server, auto-detected, any model (OLLAMA_HOST)
- **sentence-transformers**: Local, CPU/GPU, no API key
- **MLX**: Apple Silicon optimized, local, no API key

Dimension determined by model. Must be consistent across indexing and queries.

### Summarization Providers
Generate human-readable summaries from content.

- **anthropic**: LLM-based, cost-effective option (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)
- **openai**: LLM-based, high quality (OPENAI_API_KEY)
- **gemini**: LLM-based, Google (GEMINI_API_KEY)
- **ollama**: LLM-based, local server, auto-detected (OLLAMA_HOST)
- **MLX**: LLM-based, local, no API key
- **truncate**: Simple text truncation (fallback)
- **passthrough**: Store content as-is (with length limit)

**Contextual Summarization:**

When documents have user tags (domain, topic, project, etc.), the summarizer
receives context from related items. This produces summaries that highlight
relevance to the tagged context rather than generic descriptions.

How it works:
1. When processing pending summaries, the system checks for user tags
2. Finds similar items that share any of those tags (OR-union)
3. Boosts scores for items sharing multiple tags (+20% per additional match)
4. Top 5 related summaries are passed as context to the LLM
5. The summary reflects what's relevant to that context

Example: Indexing a medieval text with `domain=practice` produces a summary
highlighting its relevance to contemplative practice, not just "a 13th-century
guide for anchoresses."

**Tag changes trigger re-summarization:** When user tags are added, removed, or
changed on an existing document, it's re-queued for contextual summarization
even if content is unchanged. The existing summary is preserved until the new
one is ready.

Non-LLM providers (truncate, first_paragraph, passthrough) ignore context.

### Document Providers
Fetch content from URIs with content regularization.

- **composite**: Handles file://, https:// (default)
- Extensible for s3://, gs://, etc.

**Content Regularization:**
- **PDF**: text extracted via pypdf
- **HTML**: text extracted via BeautifulSoup (scripts/styles removed)
- **Other formats**: treated as plain text

This ensures both embedding and summarization receive clean text.

---

## Extension Points

**New Provider**
1. Implement Protocol from [providers/base.py](keep/providers/base.py)
2. Register with `get_registry().register_X("name", YourClass)`
3. Reference by name in config

**New Store Backend**
- Current: ChromaDB
- Future: Could extract Protocol from `ChromaStore`
- Candidates: PostgreSQL+pgvector, SQLite+faiss

**New Query Types**
- Add methods to `Keeper`
- Delegate to `ChromaStore` or implement in API layer

---

## Performance Characteristics

**Indexing**
- Embedding: ~50-200ms per item (local models)
- Summarization: ~100ms-2s per item (depends on provider)
- Storage: ~10ms per item

**Querying**
- Semantic search: ~10-50ms for 10k items
- Tag queries: ~1-10ms
- Full-text search: ~10-100ms

**Caching**
- Embedding cache avoids re-computing for repeated queries
- Persists across sessions in SQLite

**Scaling**
- ChromaDB handles ~100k items comfortably
- Larger datasets may benefit from PostgreSQL backend
- Embedding dimension affects memory (384d vs 1536d)

---

## Failure Modes

**Missing Dependencies**
- Registry provides clear error about which provider failed
- Lists available alternatives
- Lazy loading prevents import-time crashes

**URI Fetch Failures**
- `update()` raises `IOError` for unreachable URIs
- Original error preserved in exception chain

**Invalid Config**
- Config auto-created with detected defaults
- Validation on load with clear error messages

**Store Corruption**
- ChromaDB is resilient (SQLite-backed)
- Embedding cache can be deleted and rebuilt
- No critical data loss if store is backed up

---

## Testing Strategy

**Unit Tests**: [tests/test_core.py](tests/test_core.py)
- Data types (Item, filtering)
- Context dataclasses
- No external dependencies

**Document Store Tests**: [tests/test_document_store.py](tests/test_document_store.py)
- SQLite persistence
- Version history (archive, retrieval, navigation)
- Schema migration

**Integration Tests**: [tests/test_integration.py](tests/test_integration.py)
- End-to-end: remember → find
- Multiple collections
- Recency decay
- Embedding cache

**Provider Tests**: (TODO)
- Each provider independently
- Graceful degradation when unavailable

---

## Future Work

### Planned (in [later/](later/))
- **Relationships**: Link items with typed edges
- **Advanced Tagging**: LLM-based tag generation
- **Hierarchical Context**: Topic summaries, working context

### Under Consideration
- Multi-store facade (private/shared routing)
- Batch operations for performance
- Incremental indexing (track changes)
- Export/import for backup
- Web UI for exploration
