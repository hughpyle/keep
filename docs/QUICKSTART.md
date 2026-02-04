# Quick Start

## Installation

```bash
# Recommended: uv (isolated environment, fast)
uv tool install 'keep-skill[local]'

# Alternative: pip in a virtual environment
python -m venv .venv && source .venv/bin/activate
pip install 'keep-skill[local]'

# API-based: OpenAI (requires OPENAI_API_KEY)
uv tool install 'keep-skill[openai]'
```

## Initialize

```bash
keep init                    # Creates .keep/ at repo root
keep init --store ./data     # Custom location
```

## Basic Usage

```bash
# Index content
keep update file:///path/to/doc.md -t project=myapp
keep update "Meeting notes from today" -t type=meeting

# Search
keep find "authentication" --limit 5
keep find "auth" --since P7D           # Last 7 days

# Retrieve (shows similar items by default)
keep get file:///path/to/doc.md
keep get ID --no-similar             # Without similar items

# Tags
keep list --tag project=myapp          # Find by tag
keep list --tags=                      # List all tag keys
keep tag-update ID --tag status=done   # Update tags
```

## Current Intentions

Track what you're working on:

```bash
keep now                               # Show current intentions
keep now "Working on auth bug"         # Update intentions
keep now -V 1                          # Previous intentions
keep now --history                     # All versions
```

## Version History

All documents retain history on update:

```bash
keep get ID                  # Current version (shows prev nav)
keep get ID -V 1             # Previous version
keep get ID --history        # List all versions
```

Text updates use content-addressed IDs:
```bash
keep update "my note"              # Creates ID from content hash
keep update "my note" -t done      # Same ID, new version (tag change)
keep update "different note"       # Different ID (new document)
```

## Python API

```python
from keep import Keeper

kp = Keeper()  # Uses .keep/ at repo root

# Index
kp.update("file:///path/to/doc.md", tags={"project": "myapp"})
kp.remember("Important insight about auth patterns")

# Search
results = kp.find("authentication", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.id}: {r.summary}")

# Retrieve
item = kp.get("file:///path/to/doc.md")

# Version history
prev = kp.get_version("doc:1", offset=1)     # Previous version
versions = kp.list_versions("doc:1")          # All versions
```

## Async Summarization

For large content, summarization happens automatically in the background:

```bash
keep update file:///large-doc.md     # Stores immediately, summarizes in background
keep process-pending                  # Check/process pending summaries
```

Short content uses the text verbatim as its summary (no processing needed).

## Configuration

First run creates `.keep/keep.toml`:

```toml
[store]
version = 3

[embedding]
name = "sentence-transformers"
model = "all-MiniLM-L6-v2"

[summarization]
name = "truncate"

# Default tags for all updates
[tags]
project = "my-project"
```

## Environment Variables

```bash
KEEP_STORE_PATH=/path/to/store       # Override store location
KEEP_TAG_PROJECT=myapp               # Auto-apply tags
OPENAI_API_KEY=sk-...                # For OpenAI providers
```

## Troubleshooting

**Model download hangs:** First use downloads models (~minutes). Cached in `~/.cache/`.

**ChromaDB errors:** Delete `.keep/chroma/` to reset.

**Slow local summarization:** Large content is summarized in the background automatically.

## Next Steps

- [REFERENCE.md](REFERENCE.md) — Complete CLI and API reference
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
- [ARCHITECTURE.md](ARCHITECTURE.md) — System internals
- [SKILL.md](../SKILL.md) — The reflective practice
