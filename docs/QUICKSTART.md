# Quick Start

## Installation

```bash
pip install keep-skill
```

That's it! API SDKs for Voyage, OpenAI, Anthropic, and Gemini are included.

For local models (no API keys needed, macOS Apple Silicon):
```bash
pip install 'keep-skill[local]'
```

## Provider Configuration

### API Providers

Set environment variables for your preferred providers:

| Provider | Env Variable | Get API Key | Embeddings | Summarization |
|----------|--------------|-------------|------------|---------------|
| **Voyage AI** | `VOYAGE_API_KEY` | [dash.voyageai.com](https://dash.voyageai.com/) | ✓ | - |
| **Anthropic** | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) | - | ✓ |
| **OpenAI** | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/) | ✓ | ✓ |
| **Google Gemini** | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/) | ✓ | ✓ |

**Recommended setup** (best quality/cost balance):
```bash
export VOYAGE_API_KEY=...      # Embeddings (Anthropic's partner)
export ANTHROPIC_API_KEY=...   # Summarization (cheapest: claude-3-haiku)
keep put "test"             # Store auto-initializes on first use
```

**Single provider** (if you only have one API key):
```bash
export OPENAI_API_KEY=...      # Does both embeddings + summarization
keep put "test"
```

### Local Providers

For offline operation (macOS Apple Silicon):
```bash
pip install 'keep-skill[local]'
keep put "test"             # No API key needed
```

### Claude Desktop Setup

For use in Claude Desktop, API-based providers can be used.
For OpenAI (handles both embeddings and summarization):

1. **Get an OpenAI API key** at [platform.openai.com](https://platform.openai.com/)
2. **Add to network allowlist**: `api.openai.com`
3. **Set `OPENAI_API_KEY`** and use normally

Alternatively, for best quality embeddings with Anthropic summarization:

1. **Get API keys** at [dash.voyageai.com](https://dash.voyageai.com/) and [console.anthropic.com](https://console.anthropic.com/)
2. **Add to network allowlist**: `api.voyageai.com`, `api.anthropic.com`
3. **Set both `VOYAGE_API_KEY` and `ANTHROPIC_API_KEY`**

## Basic Usage

```bash
# Index content (files, URLs, or inline text)
keep put "file://$(keep config tool)/docs/library/ancrenewisse.pdf"
keep put https://inguz.substack.com/p/keep -t topic=practice
keep put "Meeting notes from today" -t type=meeting

# Search (returns: id date summary)
keep find "authentication" --limit 5
keep find "auth" --since P7D           # Last 7 days

# Retrieve (shows similar items by default)
keep get "file://$(keep config tool)/docs/library/ancrenewisse.pdf"
keep get https://inguz.substack.com/p/keep
keep get ID --no-similar             # Without similar items

# Tags
keep list --tag project=myapp          # Find by tag
keep list --tags=                      # List all tag keys
keep tag-update ID --tag status=done   # Update tags
```

## Reading the Output

Commands produce output in a distinctive format. Here's what to expect.

**Search results** (`keep find`) show one line per result — `id date summary`:

```
_now:default 2026-02-07 Finished reading MN61. The mirror teaching: ...
file:///.../library/mn61.html 2026-02-07 The Exhortation to Rāhula...
https://inguz.substack.com/p/keep 2026-02-07 Keep: A Reflective Memory...
file:///.../library/han_verse.txt 2026-02-07 Han Verse: Great is the matter...
```

**Full output** (`keep get`, `keep now`) uses YAML frontmatter with the document body below:

```
---
id: file:///.../library/mn61.html
tags: {_source: uri, _updated: 2026-02-07T15:14:28+00:00, topic: reflection, type: teaching}
similar:
  - https://inguz.substack.com/p/keep (0.47) 2026-02-07 Keep: A Reflective Memory...
  - _now:default (0.45) 2026-02-07 Finished reading MN61. The mirror teachi...
  - file:///.../library/han_verse.txt (0.44) 2026-02-07 Han Verse: Great is the matter...
prev:
  - @V{1} 2026-02-07 Previous version summary...
---
The Exhortation to Rāhula at Mango Stone is a Buddhist sutra that teaches...
```

Key fields:
- **`similar:`** — related items with similarity scores (0–1). Each ID can be passed to `keep get`
- **`prev:`** / **`next:`** — version navigation. `@V{1}` means "one version back", usable with `-V 1`
- **`tags:`** — user tags and system tags (`_created`, `_updated`, `_source`, etc.)

Other output formats: `--json` for machine-readable JSON, `--ids` for bare IDs only.

## Current Intentions

Track what you're working on:

```bash
keep now                               # Show current intentions
keep now "Working on auth bug"         # Update intentions
keep now -V 1                          # Previous intentions
keep now --history                     # All versions
keep reflect                           # Deep structured reflection
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
keep put "my note"              # Creates ID from content hash
keep put "my note" -t done      # Same ID, new version (tag change)
keep put "different note"       # Different ID (new document)
```

## Python API

```python
from keep import Keeper

kp = Keeper()  # Uses ~/.keep/ by default

# Index from file or URL
kp.update("file:///path/to/doc.md", tags={"project": "myapp"})
kp.update("https://inguz.substack.com/p/keep", tags={"topic": "practice"})
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

## Model Configuration

Customize models in `~/.keep/keep.toml`:

```toml
[embedding]
name = "voyage"
model = "voyage-3.5-lite"

[summarization]
name = "anthropic"
model = "claude-3-haiku-20240307"
```

### Available Models

| Provider | Type | Models |
|----------|------|--------|
| **Voyage** | Embeddings | `voyage-3.5-lite` (default), `voyage-3-large`, `voyage-code-3` |
| **Anthropic** | Summarization | `claude-3-haiku-20240307` (default, $0.25/MTok), `claude-3-5-haiku-20241022` |
| **OpenAI** | Embeddings | `text-embedding-3-small` (default), `text-embedding-3-large` |
| **OpenAI** | Summarization | `gpt-4o-mini` (default), `gpt-4o` |
| **Gemini** | Embeddings | `text-embedding-004` (default) |
| **Gemini** | Summarization | `gemini-3-flash-preview` (default), `gemini-3-pro-preview` |
| **Local** | Embeddings | `all-MiniLM-L6-v2` (sentence-transformers) |
| **Local** | Summarization | MLX models (Apple Silicon only) |

## Tool Integrations

On first use, `keep` detects coding tools and installs a protocol block and hooks into their global configuration. This happens once and is tracked in `keep.toml`.

| Tool | Protocol Block | Hooks |
|------|---------------|-------|
| Claude Code (`~/.claude/`) | `CLAUDE.md` — reflective practice prompt | `settings.json` — `keep now` on session start |
| OpenAI Codex (`~/.codex/`) | `AGENTS.md` — reflective practice prompt | — |
| Kiro (`~/.kiro/`) | detection only | detection only |

Run `keep config` to see integration status. Set `KEEP_NO_SETUP=1` to skip auto-install.

## Environment Variables

```bash
KEEP_STORE_PATH=/path/to/store       # Override store location
KEEP_TAG_PROJECT=myapp               # Auto-apply tags
KEEP_NO_SETUP=1                      # Skip auto-install of tool integrations
VOYAGE_API_KEY=pa-...                # For Voyage embeddings
ANTHROPIC_API_KEY=sk-ant-...         # For Anthropic summarization
OPENAI_API_KEY=sk-...                # For OpenAI providers
GEMINI_API_KEY=...                   # For Gemini providers
```

## Troubleshooting

**No embedding provider configured:** Set an API key (e.g., `VOYAGE_API_KEY`) or install `keep-skill[local]`.

**Model download hangs:** First use of local models downloads weights (~minutes). Cached in `~/.cache/`.

**ChromaDB errors:** Delete `~/.keep/chroma/` to reset.

**Slow local summarization:** Large content is summarized in the background automatically.

## Next Steps

- [REFERENCE.md](REFERENCE.md) — Complete CLI and API reference
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
- [ARCHITECTURE.md](ARCHITECTURE.md) — System internals
- [SKILL.md](../SKILL.md) — The reflective practice
