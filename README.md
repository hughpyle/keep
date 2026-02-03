# keep

**Semantic memory with version history.**

Index documents and notes. Search by meaning. Track changes over time.

```bash
pip install 'keep-skill[local]'
keep init

# Index content
keep update path/to/document.md -t project=myapp
keep update "Rate limit is 100 req/min" -t topic=api

# Search by meaning
keep find "what's the rate limit?"

# Track what you're working on
keep now "Debugging auth flow"
keep now -V 1                    # Previous context
```

---

## What It Does

- **Semantic search** — Find by meaning, not just keywords
- **Version history** — All documents retain history on update
- **Tag organization** — Filter and navigate with key=value tags
- **Recency decay** — Recent items rank higher in search
- **Works offline** — Local embedding models by default

Backed by ChromaDB for vectors, SQLite for metadata and versions.

---

## Installation

**Python 3.11–3.13 required.**

```bash
# Recommended: local models (works offline)
pip install 'keep-skill[local]'

# Or with uv (faster):
uv tool install 'keep-skill[local]'

# API-based alternative (requires OPENAI_API_KEY)
pip install 'keep-skill[openai]'
```

First run downloads embedding models (~3-5 minutes).

---

## Quick Start

```bash
keep init                              # Creates .keep/ at repo root

# Index files and notes
keep update file:///path/to/doc.md -t project=myapp
keep update "Important insight" -t type=note

# Search
keep find "authentication flow" --limit 5
keep find "auth" --since P7D           # Last 7 days

# Retrieve
keep get file:///path/to/doc.md
keep get ID -V 1                       # Previous version
keep get "ID@V{1}"                     # Same as -V 1 (version identifier)
keep get ID --history                  # All versions

# Tags
keep tag project=myapp                 # Find by tag
keep tag --list                        # List all tags

# Current context
keep now                               # Show what you're working on
keep now "Fixing login bug"            # Update context
```

### Python API

```python
from keep import Keeper

kp = Keeper()

# Index
kp.update("file:///path/to/doc.md", tags={"project": "myapp"})
kp.remember("Rate limit is 100 req/min", tags={"topic": "api"})

# Search
results = kp.find("rate limit", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.summary}")

# Version history
prev = kp.get_version("doc:1", offset=1)
versions = kp.list_versions("doc:1")
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for configuration and more examples.

---

## Documentation

- **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — Setup, configuration, lazy summarization
- **[docs/REFERENCE.md](docs/REFERENCE.md)** — Complete CLI and API reference
- **[docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)** — Working session patterns
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — How it works under the hood
- **[SKILL.md](SKILL.md)** — The reflective practice (for AI agents)

---

## For AI Agents

This library was designed as an agent skill — persistent memory that helps agents reflect before acting and learn from experience.

**The practice:**
- Pause before acting — `keep find` what you already know
- Notice breakdowns — when assumptions surface, index them
- Reflect after — `keep update` learnings for future sessions

See **[SKILL.md](SKILL.md)** for the full practice guide.

---

## Status

**Current:** v0.3.0

**Working:**
- ✅ Semantic search with embeddings
- ✅ Document versioning (all updates retain history)
- ✅ Content-addressed IDs for text (same content = same ID)
- ✅ Tag queries and full-text search
- ✅ Current context tracking (`keep now`)
- ✅ Recency decay (recent items rank higher)
- ✅ Lazy summarization (background processing)
- ✅ Provider abstraction (local or API-based)

**Planned** (see [later/](later/)):
- ⏳ Private/shared routing
- ⏳ Relationship graphs between items
- ⏳ LLM-based auto-tagging

---

## License

MIT

---

## Contributing

Published on [PyPI as `keep-skill`](https://pypi.org/project/keep-skill/).

Issues and PRs welcome:
- Provider implementations
- Performance improvements
- Documentation clarity

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
