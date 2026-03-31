# Architecture Overview

## What is keep?

**keep** is a reflective memory system. It gives agents a comprehensive tool for persistent indexing, tagging, entity relationship management, summarization, semantic and timeline analysis, and powerful contextual recall. It's designed as an agent skill for Claude Code, OpenClaw, LangChain/LangGraph, and other agentic environments, enabling agents to remember information across sessions over time.

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
│  MCP Layer (mcp.py)                                         │
│  - KeepFastMCP: tools, prompts, resources via stdio         │
│  - Thin HTTP proxy to daemon                                │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTP
                   ▼
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
- `FlowHostProtocol`: minimal backend-neutral boundary (`run_flow` + `close`)
- `KeeperProtocol` (extends `FlowHostProtocol`), `VectorStoreProtocol`, `DocumentStoreProtocol`, `PendingQueueProtocol`
- Enables pluggable backends (local SQLite/ChromaDB or remote PostgreSQL/pgvector)

**[flow_client.py](keep/flow_client.py)** — Shared wrapper layer
- Convenience operations (get, put, find, tag, delete, move, now) over `FlowHostProtocol.run_flow`
- Used by both `Keeper` and `RemoteKeeper` — one semantic path for local and hosted
- Parameter normalization and response coercion only; no semantic behavior

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
- HTTP client implementing `FlowHostProtocol`
- Public methods delegate through `flow_client` wrappers, same as local `Keeper`
- Connects to the hosted REST API (keepmem)

**[projections.py](keep/projections.py)** — Context projection planning
- Token-budgeted rendering plans for find-context responses
- Separates planning (what fits in the budget) from formatting (how to render)
- Used by CLI rendering to produce structured output within token limits

**[config.py](keep/config.py)** — Configuration
- Detects available providers (platform, API keys, Ollama)
- Persists choices in `keep.toml`
- Auto-creates on first use

**[pending_summaries.py](keep/pending_summaries.py)** — Background work queue
- SQLite-backed queue for deferred processing: summarization, embedding, OCR, and analysis
- Atomic dequeue with PID claims; stale claim recovery for crashed processors
- Exponential backoff on failure (30s → 1h); dead-letter for exhausted retries
- Task types: `summarize`, `embed`, `reindex`, `ocr`, `analyze`

**[types.py](keep/types.py)** — Data model
- `Item`: Immutable result type
- `PromptInfo`: Agent prompt metadata including `mcp_arguments` for MCP exposure
- System tag protection (prefix: `_`)

**[mcp.py](keep/mcp.py)** — MCP stdio server
- `KeepFastMCP` subclass of `FastMCP` — three tools (`keep_flow`, `keep_prompt`, `keep_help`)
- Dynamic prompt exposure: prompt docs tagged with `mcp_prompt` become native MCP prompts (protocol-level `list_prompts` / `get_prompt`)
- MCP resources: `keep://now` (current note) and `keep://{id}` (any note by ID)
- Thin HTTP layer — all operations delegate to the daemon via `_post` / `_get`
- Structured output: `keep_prompt` returns `CallToolResult` with `structuredContent`

**[_context_resolution.py](keep/_context_resolution.py)** — Context assembly mixin
- Prompt rendering, similar-for-display, meta-doc resolution
- `_normalize_mcp_prompt_args()`: parses `mcp_prompt` tag values (comma-separated, JSON array, or list) into validated arg tuples
- `_coerce_token_budget()`: string-to-int coercion at MCP/HTTP boundaries
- `_SUPPORTED_MCP_PROMPT_ARGS`: canonical set of allowed MCP prompt argument names

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
│ Content Regular-│ ← Extract text from HTML/PDF/DOCX/PPTX
│ ization         │   (scripts/styles removed)
└────────┬────────┘
         │ clean text (+ OCR page list if scanned)
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
         │
         ▼ (if scanned PDF or image)
┌─────────────────────────────────┐
│ Background OCR (keep pending)   │
│ Placeholder stored immediately; │
│ OCR text replaces it + re-embeds│
└─────────────────────────────────┘
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
- Meta-tags resolve related context at retrieval time

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
- **mistral**: API-based (MISTRAL_API_KEY)
- **ollama**: Local server, auto-detected, any model (OLLAMA_HOST)
- **sentence-transformers**: Local, CPU/GPU, no API key
- **MLX**: Apple Silicon optimized, local, no API key

Dimension determined by model. Must be consistent across indexing and queries.

### Summarization Providers
Generate human-readable summaries from content.

- **anthropic**: LLM-based, cost-effective option (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)
- **openai**: LLM-based, high quality (OPENAI_API_KEY)
- **gemini**: LLM-based, Google (GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT for Vertex AI)
- **mistral**: LLM-based (MISTRAL_API_KEY)
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
- **PDF**: text extracted via pypdf; scanned pages (no extractable text) flagged for background OCR
- **HTML**: text extracted via BeautifulSoup (scripts/styles removed)
- **DOCX/PPTX**: text + tables/slides extracted via python-docx/python-pptx; auto-tags: author, title
- **Audio** (MP3, FLAC, OGG, WAV, AIFF, M4A, WMA): metadata via tinytag; auto-tags: artist, album, genre, year
- **Images** (JPEG, PNG, TIFF, WEBP): EXIF metadata via Pillow; auto-tags: dimensions, camera, date; flagged for background OCR
- **Other formats**: treated as plain text

Provider-extracted tags merge with user tags (user wins on collision). This ensures both embedding and summarization receive clean text.

### Content Extractor / OCR Providers
Extract text from scanned PDFs and images via optical character recognition.

- **mistral**: Cloud OCR via `mistral-ocr-latest` — high quality, images and PDFs (MISTRAL_API_KEY)
- **ollama**: Uses `glm-ocr` model (auto-pulled on first use)
- **mlx**: Apple Silicon — uses `mlx-vlm` vision models

OCR runs in the background via the pending queue (`keep pending`), not during `put()`. The flow:

1. During `put()`, content regularization detects scanned PDF pages (no extractable text) or image files
2. A placeholder is stored immediately so the item is indexed right away
3. The pages/image are enqueued for background OCR processing
4. `keep pending` picks up the OCR task, renders pages to images, runs OCR, cleans and scores the text
5. The full OCR text replaces the placeholder and the item is re-embedded

Design points:
- Auto-detected: Ollama (with `glm-ocr`) > MLX > None. No configuration needed.
- Security: Pillow decompression bomb guard (250MP limit), PDF page cap (1000), temp directory cleanup
- OCR text is cleaned (whitespace normalized) and confidence-scored
- Graceful degradation: no OCR provider = metadata-only indexing (unchanged behavior)

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
