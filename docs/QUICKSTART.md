# Quick Start

## Installation

```bash
# Recommended: Install with local models (sentence-transformers)
# Includes sentence-transformers + MLX on Apple Silicon
pip install 'assocmem[local]'

# Minimal: Core only (you'll need to configure providers manually)
pip install assocmem

# API-based: Use OpenAI for embeddings (requires OPENAI_API_KEY)
pip install 'assocmem[openai]'

# Development: Include test tools
pip install 'assocmem[dev]'
```

**What gets installed:**

| Extra | Dependencies | Use case |
|-------|--------------|----------|
| `[local]` | sentence-transformers, MLX (Apple Silicon only) | Best default - works offline |
| `[openai]` | openai SDK | API-based, requires key, high quality |
| `[dev]` | pytest, pytest-cov | For running tests |
| (none) | chromadb, typer, tomli-w | Minimal - configure providers yourself |

## Basic Usage

```python
from assocmem import AssociativeMemory

# Initialize (creates .assocmem/ at git repo root by default)
mem = AssociativeMemory()

# Index a document from URI
item = mem.update("file:///path/to/document.md")

# Remember inline content
item = mem.remember("Important insight about authentication patterns")

# Semantic search
results = mem.find("how does authentication work?", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.id}: {r.summary}")

# Get specific item
item = mem.get("file:///path/to/document.md")

# Tag-based lookup
docs = mem.query_tag("project", "myapp")

# Check if item exists
if mem.exists("file:///path/to/document.md"):
    print("Already indexed")
```

## CLI Usage

```bash
# Initialize store
python -m assocmem init

# Index a document
python -m assocmem update file:///path/to/doc.md -t project=myapp

# Remember inline content
python -m assocmem remember "Meeting notes from today" -t type=meeting

# Search
python -m assocmem find "authentication" --limit 5

# Get by ID
python -m assocmem get file:///path/to/doc.md

# Tag lookup
python -m assocmem tag project myapp

# List collections
python -m assocmem collections

# Output as JSON
python -m assocmem find "auth" --json
```

## Configuration

First run auto-detects best providers and creates `.assocmem/assocmem.toml`:

```toml
[store]
version = 1
created = "2026-01-30T12:00:00Z"

[embedding]
name = "sentence-transformers"
model = "all-MiniLM-L6-v2"

[summarization]
name = "truncate"
max_length = 500

[document]
name = "composite"
```

Edit to customize providers or models.

## Working with Collections

Collections partition the store into separate namespaces:

```python
# Specify collection
mem = AssociativeMemory(collection="work")

# Or pass at operation time
mem.remember("Note", collection="personal")
results = mem.find("query", collection="work")
```

## Recency Decay

Items decay in relevance over time (ACT-R model):

```python
# Configure half-life (default: 30 days)
mem = AssociativeMemory(decay_half_life_days=7.0)

# Disable decay
mem = AssociativeMemory(decay_half_life_days=0)
```

After one half-life, an item's effective score is multiplied by 0.5.

## Error Handling

```python
try:
    item = mem.update("https://unreachable.com/doc")
except IOError as e:
    print(f"Failed to fetch: {e}")

try:
    mem = AssociativeMemory(collection="Invalid-Name!")
except ValueError as e:
    print(f"Invalid collection name: {e}")
```

## Environment Variables

```bash
# Store location
export ASSOCMEM_STORE_PATH=/path/to/store

# OpenAI API key (if using openai provider)
export OPENAI_API_KEY=sk-...
# or
export ASSOCMEM_OPENAI_API_KEY=sk-...
```

## Common Patterns

### Indexing Files from a Directory

```python
from pathlib import Path

for file in Path("docs").rglob("*.md"):
    uri = file.as_uri()
    if not mem.exists(uri):
        mem.update(uri, source_tags={"type": "documentation"})
```

### Tagging Strategy

```python
# Source tags (provided at index time)
mem.update(uri, source_tags={
    "project": "myapp",
    "module": "auth",
    "language": "python"
})

# System tags (auto-managed, prefixed with _)
item.tags["_created"]      # ISO timestamp
item.tags["_updated"]      # ISO timestamp
item.tags["_content_type"] # MIME type
item.tags["_source"]       # "uri" or "inline"
```

### Search Refinement

```python
# Broad semantic search
results = mem.find("database connection")

# Narrow by tags
results = mem.query_tag("module", "database")

# Full-text search in summaries
results = mem.query_fulltext("PostgreSQL")
```

## Provider Options

### Embedding Providers

**sentence-transformers** (default)
- Local, no API key
- CPU or GPU
- Model: all-MiniLM-L6-v2 (384 dims)

**MLX** (Apple Silicon)
- Local, Metal-accelerated
- Requires: `pip install mlx sentence-transformers`
- Configure in toml: `name = "mlx"`

**OpenAI**
- API-based, requires key
- High quality, fast
- Model: text-embedding-3-small (1536 dims)

### Summarization Providers

**truncate** (default)
- Fast, zero dependencies
- Simple text truncation

**first_paragraph**
- Extracts first meaningful chunk
- Better for structured docs

**MLX** (Apple Silicon)
- LLM-based, local
- Requires: `pip install mlx-lm`
- Model: Llama-3.2-3B-Instruct-4bit

**OpenAI**
- LLM-based, API
- Requires key
- Model: gpt-4o-mini

## Troubleshooting

**Import fails with missing dependency**
- Providers are lazy-loaded now
- Only fails when you try to create AssociativeMemory
- Error message shows available providers and what's missing

**Model download hangs**
- First use downloads models (sentence-transformers, MLX)
- Can take minutes depending on connection
- Models cached in `~/.cache/huggingface/` or `~/.cache/mlx/`

**ChromaDB errors**
- Delete `.assocmem/chroma/` to reset
- Embedding cache can be deleted: `.assocmem/embedding_cache.db`

**Slow queries**
- Check embedding dimension (lower is faster)
- Reduce collection size or increase decay rate
- Consider batch operations

## Next Steps

- See [SKILL.md](../SKILL.md) for agent-focused patterns
- See [ARCHITECTURE.md](ARCHITECTURE.md) for internals
- See [REFERENCE.md](REFERENCE.md) for complete API
