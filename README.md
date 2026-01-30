# assocmem

**Semantic memory for AI agents** — persistent associative storage with vector similarity search.

---

## What is this?

A Python library that gives agents the ability to remember and recall information semantically across sessions. Think of it as "long-term memory with search" for AI agents.

```python
from assocmem import AssociativeMemory

mem = AssociativeMemory()

# Remember something
mem.remember("User prefers OAuth2 with PKCE for authentication")

# Find it later by meaning, not keywords
results = mem.find("how should we handle auth?")
# → Returns the OAuth2 note, even though "OAuth2" wasn't in the query
```

**Key features:**
- Semantic search using embeddings (not just keyword matching)
- Persistent across sessions (backed by ChromaDB)
- Tag-based organization and filtering
- Recency decay (recent items rank higher)
- Provider abstraction (local models or APIs)
- CLI and Python API

**Use cases:**
- Agent memory across conversations
- Indexing project documentation for quick lookup
- Building knowledge bases with semantic search
- Caching research findings by topic

---

## Quick Start

```bash
# Recommended: Install with local models
pip install 'assocmem[local]'

# Or minimal install (configure providers manually)
pip install assocmem
```

```python
from assocmem import AssociativeMemory

mem = AssociativeMemory()

# Index a file
mem.update("file:///path/to/document.md", source_tags={"project": "myapp"})

# Remember inline content
mem.remember("Important: rate limit is 100 req/min", source_tags={"topic": "api"})

# Semantic search
results = mem.find("what's the rate limit?", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.summary}")

# Tag lookup
api_docs = mem.query_tag("topic", "api")
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for more examples.

---

## Documentation

- **[SKILL.md](SKILL.md)** — OpenClaw skill reference (CLI commands)
- **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — Installation, basic usage, configuration
- **[docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)** — Detailed agent patterns, Python API
- **[docs/REFERENCE.md](docs/REFERENCE.md)** — Complete API reference
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — How it works under the hood
- **[docs/patterns/](docs/patterns/)** — Domain and conversation patterns

---

## Design Philosophy

**1. Schema as Data**
System configuration and behavior are stored as queryable documents, not hardcoded. This enables agents to update the system's rules.

**2. Semantic by Default**
Search by meaning, not keywords. Items are embedded and retrieved by vector similarity.

**3. Lazy Loading**
Optional dependencies are loaded only when needed. Missing providers give helpful error messages instead of import-time crashes.

**4. Provider Agnostic**
Pluggable backends for embeddings (sentence-transformers, OpenAI, MLX), summarization (truncate, LLM-based), and storage (ChromaDB, extensible).

**5. No Original Content**
Only summaries and embeddings are stored. Reduces size, forces meaningful summarization, URIs can be re-fetched if needed.

---

## Status

**Current**: v0.1.0 — Early draft

**Working:**
- ✅ Core indexing (`update`, `remember`)
- ✅ Semantic search (`find`, `find_similar`)
- ✅ Tag queries and full-text search
- ✅ Embedding cache for performance
- ✅ Recency decay (ACT-R style)
- ✅ CLI interface
- ✅ Provider abstraction with lazy loading

**Planned** (see [later/](later/)):
- ⏳ Context management (working context, top-of-mind retrieval)
- ⏳ Private/shared routing
- ⏳ Relationship graphs between items
- ⏳ LLM-based tagging

---

## Requirements

- Python 3.11+
- ChromaDB (vector store)
- One embedding provider:
  - sentence-transformers (local, default)
  - MLX (Apple Silicon, local)
  - OpenAI (API, requires key)

---

## License

MIT

---

## Contributing

This is an early draft. Issues and PRs welcome, especially for:
- Additional provider implementations
- Performance improvements
- Documentation clarity
- OpenClaw integration patterns

---

## Related Projects

- [ChromaDB](https://github.com/chroma-core/chroma) — Vector database backend
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) — Embedding models
- [MLX](https://github.com/ml-explore/mlx) — Apple Silicon ML framework
- [OpenClaw](https://openclaw.dev) — Agent framework (integration target)
