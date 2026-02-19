# Architecture Overview

## What is keep?

**keep** is a reflective memory system providing persistent storage with vector similarity search. It's designed as an agent skill for Claude Code, OpenClaw, LangChain/LangGraph, and other agentic environments, enabling agents to remember information across sessions over time.

Think of it as: **vector search + embeddings + summarization + tagging** wrapped in a simple API.

Published by Hugh Pyle, "inguz ᛜ outcomes", under the MIT license.
Contributions are welcome; code is conversation, "right speech" is encouraged.

---

## Core Concept

Every stored item has:
- **ID**: URI or custom identifier
- **Summary**: Human-readable text (stored, searchable)
- **Embedding**: Vector representation (for semantic search)
- **Tags**: Key-value metadata (for filtering)
- **Timestamps**: Created/updated/accessed (auto-managed)
- **Version History**: Previous versions archived automatically on update
- **Parts**: Optional structural decomposition (from `analyze`)

The original document content is **not stored** — only the summary and embedding.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  API Layer (api.py)                                         │
│  - Keeper class                                             │
│  - High-level operations: put(), find(), get()              │
│  - Version management: get_version(), list_versions()       │
│  - Structural analysis: analyze()                           │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┼──────────┬──────────┬──────────┬───────────┐
        │          │          │          │          │           │
        ▼          ▼          ▼          ▼          ▼           ▼
   ┌────────┐ ┌─────────┐ ┌────────┐ ┌────────┐ ┌─────────┐ ┌─────────┐
   │Document│ │Embedding│ │Summary │ │Media   │ │Vector   │ │Document │
   │Provider│ │Provider │ │Provider│ │Descr.  │ │Store    │ │Store    │
   └────────┘ └─────────┘ └────────┘ └────────┘ └─────────┘ └─────────┘
       │          │           │          │             │           │
   fetch()    embed()    summarize()  describe()  vectors/    summaries/
   from URI   text→vec  text→summary  media→text  search      versions
```

### Components

**[api.py](keep/api.py)** — Main facade
- `Keeper` class
- Coordinates providers and stores
- Implements query operations with recency decay
- Content-based embedding dedup (skips re-embedding when content matches an existing document)

**[protocol.py](keep/protocol.py)** — Abstract interfaces
- `KeeperProtocol`, `VectorStoreProtocol`, `DocumentStoreProtocol`, `PendingQueueProtocol`
- Enables pluggable backends (local SQLite/ChromaDB or remote PostgreSQL/pgvector)

**[store.py](keep/store.py)** — Vector persistence (local)
- `ChromaStore` wraps ChromaDB
- Handles vector storage, similarity search, metadata queries
- Versioned embeddings: `{id}@v{N}` for history
- Part embeddings: `{id}@p{N}` for structural decomposition

**[document_store.py](keep/document_store.py)** — Document persistence (local)
- `DocumentStore` wraps SQLite
- Stores summaries, tags, timestamps, content hashes
- Version history: archives previous versions on update
- Parts table: structural decomposition from `analyze`

**[backend.py](keep/backend.py)** — Pluggable storage factory
- Creates store backends based on configuration
- External backends register via `keep.backends` entry point
- Returns `StoreBundle` (doc store, vector store, pending queue)

**[remote.py](keep/remote.py)** — Remote client
- HTTP client implementing `KeeperProtocol`
- Connects to the hosted REST API (keepmem)

**[config.py](keep/config.py)** — Configuration
- Detects available providers (platform, API keys, Ollama)
- Persists choices in `keep.toml`
- Auto-creates on first use

**[pending_summaries.py](keep/pending_summaries.py)** — Deferred processing
- Queue for background summarization and embedding
- Used in cloud mode where embedding happens server-side

**[types.py](keep/types.py)** — Data model
- `Item`: Immutable result type
- System tag protection (prefix: `_`)

---

## Data Flow

### Indexing: put(uri=...) or put(content)

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
         ▼
┌─────────────────┐
│ Media Enrichment│ ← Optional: vision description (images)
│ (if configured) │   or transcription (audio) appended
└────────┬────────┘
         │ enriched text
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
│ DocumentStore   │  │ VectorStore     │
│ upsert()        │  │ upsert()        │
│ - summary       │  │ - embedding     │
│ - tags          │  │ - summary       │
│ - timestamps    │  │ - tags          │
│ - content hash  │  │ - version embed │
│ - archive prev  │  │                 │
└─────────────────┘  └─────────────────┘
```

**Versioning on update:**
- DocumentStore archives current version before updating
- VectorStore adds versioned embedding (`{id}@v{N}`) if content changed
- Same content (hash match) skips duplicate embedding

**Embedding dedup:**
- Before computing an embedding, checks if another document has the same content hash
- If a donor exists with a compatible embedding, copies it instead of re-embedding
- Safety: dimension check prevents cross-model contamination

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
│ VectorStore       │
│ query_embedding() │ ← cosine similarity search
└─────────┬─────────┘
          │
          ▼ results with distance scores
    ┌──────────────┐
    │ Apply decay  │ ← Recency weighting (ACT-R style)
    │ score × 0.5^(days/half_life)
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │ Date filter  │ ← Optional --since / --until
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
            ├─ get archived embedding from VectorStore (id@vN)
            ├─ restore_latest_version() in DocumentStore
            │    (promote latest version row to current, delete version row)
            ├─ upsert restored embedding as current in VectorStore
            └─ delete versioned entry (id@vN) from VectorStore
```

---

## Key Design Decisions

**1. Schema as Data**
- System configuration stored as documents in the store (e.g. `.now`, `.tag/*`)
- Enables agents to query and update behavior through the same API
- Meta-documents resolve related context at retrieval time

**2. Lazy Provider Loading**
- Providers registered at first use, not import time
- Avoids crashes when optional dependencies missing
- Better error messages about what's needed

**3. Separation of Concerns**
- Store is provider-agnostic (only knows about vectors/metadata)
- Providers are store-agnostic (only know about text→vectors)
- Protocols define the boundary; implementations are pluggable

**4. No Original Content Storage**
- Reduces storage size
- Forces meaningful summarization
- URIs can be re-fetched if needed

**5. Immutable Items**
- `Item` is frozen dataclass
- Updates via `put()` return new Item
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
├── document_store.db       # SQLite store (summaries, tags, versions, parts)
│   ├── documents           # Current version of each document
│   ├── document_versions   # Archived previous versions
│   └── parts               # Structural decomposition (from analyze)
└── embedding_cache.db      # SQLite cache for embeddings
```

---

## Provider Types

### Embedding Providers
Generate vector representations for semantic search.

- **gemini**: API-based, Google (GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT for Vertex AI)
- **voyage**: API-based, Anthropic's recommended partner (VOYAGE_API_KEY)
- **openai**: API-based, high quality (OPENAI_API_KEY)
- **ollama**: Local server, auto-detected, any model (OLLAMA_HOST)
- **sentence-transformers**: Local, CPU/GPU, no API key
- **MLX**: Apple Silicon optimized, local, no API key

Dimension determined by model. Must be consistent across indexing and queries.

### Summarization Providers
Generate human-readable summaries from content.

- **anthropic**: LLM-based, cost-effective option (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)
- **openai**: LLM-based, high quality (OPENAI_API_KEY)
- **gemini**: LLM-based, Google (GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT for Vertex AI)
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
- **DOCX/PPTX**: text + tables/slides extracted via python-docx/python-pptx; auto-tags: author, title
- **Audio** (MP3, FLAC, OGG, WAV, AIFF, M4A, WMA): metadata via tinytag; auto-tags: artist, album, genre, year
- **Images** (JPEG, PNG, TIFF, WEBP): EXIF metadata via Pillow; auto-tags: dimensions, camera, date
- **Other formats**: treated as plain text

Provider-extracted tags merge with user tags (user wins on collision). This ensures both embedding and summarization receive clean text.

### Media Description Providers (optional)
Generate text descriptions from media files, enriching metadata-only content.

- **mlx**: Apple Silicon — vision (mlx-vlm) + audio transcription (mlx-whisper)
- **ollama**: Local server — vision models only (llava, moondream, bakllava)

Media description runs in `Keeper.put()` between fetch and upsert. Descriptions are appended to the metadata content before embedding/summarization, making media files semantically searchable by their visual or audio content.

Design points:
- Only triggered for non-text content types (image/*, audio/*)
- Lazy sub-provider loading: MLX composite only loads VLM for first image, whisper for first audio
- GPU-locked via `LockedMediaDescriber` (same file-lock pattern as summarization)
- Graceful degradation: errors never block indexing; no provider = metadata-only (unchanged behavior)
- Optional dependency: `pip install keep-skill[media]` for MLX models

---

## LangChain / LangGraph Integration

The `keep.langchain` module provides framework adapters on top of the API layer:

```
┌─────────────────────────────────────────────────────────────┐
│  LangChain Layer (keep/langchain/)                          │
│  - KeepStore         LangGraph BaseStore adapter            │
│  - KeepNotesToolkit  4 LangChain tools                     │
│  - KeepNotesRetriever  BaseRetriever with now-context       │
│  - KeepNotesMiddleware  LCEL runnable for auto-injection    │
└──────────────────┬──────────────────────────────────────────┘
                   │ uses Keeper API
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  API Layer (api.py)                                         │
```

KeepStore maps LangGraph's namespace/key model to Keep's tag system via configurable `namespace_keys`. Namespace components become regular Keep tags, visible to CLI and all query methods. Tag filtering is a **pre-filter on the vector search**, making tags suitable for data isolation (per-user, per-project). See [LANGCHAIN-INTEGRATION.md](LANGCHAIN-INTEGRATION.md).

---

## Extension Points

**New Embedding or Summarization Provider**
1. Implement the provider protocol (EmbeddingProvider or SummarizationProvider)
2. Register in the config registry
3. Reference by name in `keep.toml`

**New Store Backend**
- Protocols defined in [protocol.py](keep/protocol.py): `VectorStoreProtocol`, `DocumentStoreProtocol`, `PendingQueueProtocol`
- Local: ChromaDB + SQLite (built-in)
- Remote: PostgreSQL + pgvector (keepmem package, registered via `keep.backends` entry point)
- Register new backends via `keep.backends` entry point in pyproject.toml

**Framework Integration**
- Implement adapters on top of the Keeper API layer
- Current: LangChain/LangGraph ([keep/langchain/](keep/langchain/))
- Pattern: map framework concepts to Keep tags + search
