# keep

**Reflective memory with version history.**

Index documents and notes. Search by meaning. Track changes over time.

```bash
pip install keep-skill
export VOYAGE_API_KEY=...        # Or OPENAI_API_KEY, GEMINI_API_KEY

# Index content (store auto-initializes on first use)
keep update https://inguz.substack.com/p/keep -t topic=practice
keep update "file://$(keep config tool)/docs/library/impermanence_verse.txt" -t type=teaching
keep update "Rate limit is 100 req/min" -t topic=api

# Search by meaning
keep find "what's the rate limit?"

# Track what you're working on
keep now "Debugging auth flow"
keep now -V 1                    # Previous intentions
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
pip install keep-skill
```

API SDKs for Voyage, OpenAI, Anthropic, and Gemini are included. Set an API key:

```bash
export VOYAGE_API_KEY=...      # Recommended (Anthropic's partner)
# Or: export OPENAI_API_KEY=... or GEMINI_API_KEY=...
```

For local models (no API keys needed):
```bash
pip install 'keep-skill[local]'   # macOS Apple Silicon optimized
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for all provider options.

---

## Quick Start

```bash
# Index URLs, files, and notes (store auto-initializes on first use)
keep update https://inguz.substack.com/p/keep -t topic=practice
keep update "file://$(keep config tool)/docs/library/impermanence_verse.txt" -t type=teaching
keep update "Token refresh needs clock sync" -t topic=auth

# Search
keep find "authentication flow" --limit 5
keep find "auth" --since P7D           # Last 7 days

# Retrieve
keep get file:///path/to/doc.md
keep get ID -V 1                       # Previous version
keep get "ID@V{1}"                     # Same as -V 1 (version identifier)
keep get ID --history                  # All versions

# Tags
keep list --tag project=myapp          # Find by tag
keep find "auth" -t topic=auth         # Cross-project topic search
keep list --tags=                      # List all tag keys

# Current intentions
keep now                               # Show what you're working on
keep now "Fixing login bug"            # Update intentions
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

- **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — Setup, configuration, async summarization
- **[docs/REFERENCE.md](docs/REFERENCE.md)** — Complete CLI and API reference
- **[docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)** — Working session patterns
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — How it works under the hood
- **[SKILL.md](SKILL.md)** — The reflective practice (for AI agents)

---

## Background

Why build memory for AI agents? What does "reflective practice" mean here?

I wrote a story: **[Wisdom, or Prompt-Engineering?](https://inguz.substack.com/p/keep)**

---

## For AI Agents

This library was designed as an agent skill — persistent memory that helps agents reflect before acting and learn from experience.

**The practice:**
- Before acting, use `keep` to reflect on the context.
- Notice breakdowns — when assumptions surface, index them.
- After acting, use `keep` to save your context and intentions.

See **[SKILL.md](SKILL.md)** for the full practice guide.

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
