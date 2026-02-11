# Quick Start

## Installation

Use [uv](https://docs.astral.sh/uv/) (recommended) or pip.

To use local MLX models on macOS Apple Silicon (no API keys needed):
```bash
uv tool install 'keep-skill[local]'
```

For all others:
```bash
uv tool install keep-skill
```

That's it! API providers for Voyage, OpenAI, Anthropic, and Gemini are included.


## Provider Configuration

### API Providers

Set environment variables for your preferred providers:

| Provider | Env Variable | Get API Key | Embeddings | Summarization |
|----------|--------------|-------------|------------|---------------|
| **Voyage AI** | `VOYAGE_API_KEY` | [dash.voyageai.com](https://dash.voyageai.com/) | ✓ | - |
| **Anthropic** | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`* | [console.anthropic.com](https://console.anthropic.com/) | - | ✓ |
| **OpenAI** | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/) | ✓ | ✓ |
| **Google Gemini** | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/) | ✓ | ✓ |

\* **Anthropic Authentication Methods:**
- **API Key** (`ANTHROPIC_API_KEY`): Recommended. Get from [console.anthropic.com](https://console.anthropic.com/). Format: `sk-ant-api03-...`
- **OAuth Token** (`CLAUDE_CODE_OAUTH_TOKEN`): For Claude Pro/Team subscribers. Generate via `claude setup-token`. Format: `sk-ant-oat01-...`
  - Note: OAuth tokens from `claude setup-token` are primarily designed for Claude Code CLI authentication
  - Direct API access with OAuth tokens may have limitations or require additional configuration
  - For production use with `keep`, prefer using a standard API key from the Anthropic console

**Simplest setup** (single API key):
```bash
export OPENAI_API_KEY=...      # Does both embeddings + summarization
# Or: GEMINI_API_KEY=...       # Also does both
keep put "test"             # Store auto-initializes on first use
```

**Best quality** (two API keys for optimal embeddings):
```bash
export VOYAGE_API_KEY=...      # Embeddings (Anthropic's partner)
export ANTHROPIC_API_KEY=...   # Summarization (cost-effective: claude-3-haiku)
# Or: CLAUDE_CODE_OAUTH_TOKEN  # OAuth token alternative
keep put "test"
```

### Ollama (Local LLM Server)

If [Ollama](https://ollama.com/) is running locally with models pulled, keep auto-detects it — no configuration needed:
```bash
ollama pull llama3.2:3b             # Any model works
keep put "test"                     # Auto-detected on first run
```

Keep picks the best available model: dedicated embedding models (e.g. `nomic-embed-text`) for embeddings, generative models (e.g. `llama3.2`) for summarization. Respects `OLLAMA_HOST` if set.

### Local Providers (Apple Silicon)

For offline operation on macOS Apple Silicon without Ollama:
```bash
uv tool install 'keep-skill[local]'
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
keep put "some content" --suggest-tags  # Show tag suggestions from similar items

# Search (returns: id date summary)
keep find "authentication" --limit 5
keep find "auth" --since P7D           # Last 7 days

# Retrieve (shows similar items by default)
keep get "file://$(keep config tool)/docs/library/ancrenewisse.pdf"
keep get https://inguz.substack.com/p/keep
keep get ID --meta                   # List meta items only
keep get ID --similar                # List similar items only

# Tags
keep list --tag project=myapp          # Find by tag
keep list --tags=                      # List all tag keys
keep tag-update ID --tag status=done   # Update tags
```

## Reading the Output

Commands produce output in a distinctive format. Here's what to expect.

**Search results** (`keep find`) show one line per result — `id date summary`:

```
now 2026-02-07 Finished reading MN61. The mirror teaching: ...
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
  - now (0.45) 2026-02-07 Finished reading MN61. The mirror teachi...
  - file:///.../library/han_verse.txt (0.44) 2026-02-07 Han Verse: Great is the matter...
meta/todo:
  - %a1b2c3d4 Update auth docs for new flow
meta/learnings:
  - %e5f6g7h8 JSON validation before deploy
prev:
  - @V{1} 2026-02-07 Previous version summary...
---
The Exhortation to Rāhula at Mango Stone is a Buddhist sutra that teaches...
```

Key fields:
- **`similar:`** — related items with similarity scores (0–1). Each ID can be passed to `keep get`
- **`meta/*:`** — contextual items from tag queries (open commitments, learnings, decisions)
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

For embedding keep into applications, see [PYTHON-API.md](PYTHON-API.md).

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
| **Ollama** | Embeddings | Any model; prefer `nomic-embed-text`, `mxbai-embed-large` |
| **Ollama** | Summarization | Any generative model (e.g. `llama3.2`, `mistral`, `phi3`) |
| **Local** | Embeddings | `all-MiniLM-L6-v2` (sentence-transformers) |
| **Local** | Summarization | MLX models (Apple Silicon only) |

## Tool Integrations

On first use, `keep` detects coding tools and installs a protocol block and hooks into their global configuration. This happens once and is tracked in `keep.toml`.

| Tool | Protocol Block | Hooks |
|------|---------------|-------|
| Claude Code (`~/.claude/`) | `CLAUDE.md` — reflective practice prompt | `settings.json` — session start, prompt submit, subagent, session end |
| Kiro (`~/.kiro/`) | `steering/keep.md` — reflective practice prompt | `hooks/*.kiro.hook` — agent spawn, prompt submit, agent stop |
| OpenAI Codex (`~/.codex/`) | `AGENTS.md` — reflective practice prompt | — |
| OpenClaw (cwd) | `AGENTS.md` — reflective practice prompt (if found in cwd) | [Plugin](OPENCLAW-INTEGRATION.md) — agent start, agent stop |

Hooks inject `keep now` context at key moments (session start, prompt submit) so the agent always has current intentions and relevant context. The protocol block teaches the reflective practice itself.

Run `keep config` to see integration status. Set `KEEP_NO_SETUP=1` to skip auto-install.

## Environment Variables

```bash
KEEP_STORE_PATH=/path/to/store       # Override store location
KEEP_TAG_PROJECT=myapp               # Auto-apply tags
KEEP_NO_SETUP=1                      # Skip auto-install of tool integrations
OLLAMA_HOST=http://localhost:11434   # Ollama server URL (auto-detected)
OPENAI_API_KEY=sk-...                # For OpenAI (embeddings + summarization)
GEMINI_API_KEY=...                   # For Gemini (embeddings + summarization)
VOYAGE_API_KEY=pa-...                # For Voyage embeddings only
ANTHROPIC_API_KEY=sk-ant-...         # For Anthropic summarization only
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...  # OAuth token alternative
```

## Troubleshooting

**No embedding provider configured:** Set an API key (e.g., `VOYAGE_API_KEY`) or install `keep-skill[local]`.

**Model download hangs:** First use of local models downloads weights (~minutes). Cached in `~/.cache/`.

**ChromaDB errors:** Delete `~/.keep/chroma/` to reset.

**Slow local summarization:** Large content is summarized in the background automatically.

**Claude Code hooks need `jq`:** The prompt-submit hook uses `jq` to extract context. Install with your package manager (e.g., `brew install jq`). Hooks are fail-safe without it, but prompt context won't be captured.

## Next Steps

- [REFERENCE.md](REFERENCE.md) — Complete CLI reference
- [PYTHON-API.md](PYTHON-API.md) — Python API for embedding keep in applications
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
- [ARCHITECTURE.md](ARCHITECTURE.md) — System internals
- [OPENCLAW-INTEGRATION.md](OPENCLAW-INTEGRATION.md) — OpenClaw plugin setup
- [SKILL.md](../SKILL.md) — The reflective practice
