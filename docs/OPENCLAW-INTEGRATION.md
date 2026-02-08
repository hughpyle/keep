# OpenClaw Integration

**Status:** Available in v0.1.0+

---

## Overview

keep can automatically integrate with OpenClaw's configured models when both are present. This enables:

- **Unified model configuration** — Configure once in OpenClaw, use everywhere
- **Local-first by default** — Embeddings stay local, summarization can use configured LLM
- **Seamless fallback** — Works standalone without OpenClaw

---

## How It Works

### Detection Priority

**For embeddings**, keep checks in this order:

1. **Voyage** — if `VOYAGE_API_KEY` set (Anthropic's recommended partner)
2. **OpenAI** — if `OPENAI_API_KEY` set
3. **Gemini** — if `GEMINI_API_KEY` set
4. **Ollama** — if running locally with models (auto-detected)
5. **MLX** — Apple Silicon local models
6. **Fallback** — sentence-transformers (local, always works)

**For summarization**, keep checks in this order:

1. **Anthropic** — if `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` set
2. **OpenAI** — if `OPENAI_API_KEY` set
3. **Gemini** — if `GEMINI_API_KEY` set
4. **Ollama** — if running locally with a generative model
5. **MLX** — Apple Silicon local models
6. **Fallback** — truncate (first 500 chars)

### What Gets Shared

**From OpenClaw config:**
- **Embedding provider** from `memorySearch.provider` (openai, gemini, or auto)
- **Embedding model** from `memorySearch.model`
- **Model selection for summarization** (e.g., `anthropic/claude-sonnet-4-5`)
- Provider routing (automatically detects Anthropic models)

**Stays local:**
- **Store** remains in `~/.keep/` (not shared with OpenClaw)
- Falls back to **sentence-transformers** if no API keys available

**API keys** are resolved from:
1. `memorySearch.remote.apiKey` in config
2. Environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`)

---

## Setup

### Option 1: API Providers (Recommended)

```bash
# 1. Install keep (API SDKs included)
uv tool install keep-skill

# 2. Set API key(s) - simplest is OpenAI (does both embeddings + summarization)
export OPENAI_API_KEY=sk-...

# 3. First use auto-initializes
keep put "test note"
```

For best quality embeddings with Anthropic summarization:
```bash
export VOYAGE_API_KEY=...       # Embeddings (Anthropic's partner)
export ANTHROPIC_API_KEY=...    # Summarization (or CLAUDE_CODE_OAUTH_TOKEN)
keep put "test note"
```

### Option 2: Local Models (No API)

```bash
uv tool install 'keep-skill[local]'
keep put "test note"         # MLX on Apple Silicon, sentence-transformers elsewhere
```

### Option 3: Manual Override

Set `OPENCLAW_CONFIG` to use a different config file:

```bash
export OPENCLAW_CONFIG=/custom/path/to/openclaw.json
keep put "test note"
```

---

## Configuration Files

### OpenClaw Config Location

Default: `~/.openclaw/openclaw.json`

**Relevant fields:**
```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "anthropic/claude-sonnet-4-5"
      }
    }
  }
}
```

### keep Config Location

Created at: `~/.keep/keep.toml` (user home)

**Example (OpenClaw integration active):**
```toml
[store]
version = 2
created = "2026-01-30T12:00:00Z"

[embedding]
name = "sentence-transformers"

[summarization]
name = "anthropic"
model = "claude-sonnet-4-20250514"

[document]
name = "composite"
```

---

## Model Mapping

OpenClaw uses short model names. keep maps them to actual Anthropic API names:

| OpenClaw Model | Anthropic API Model |
|----------------|---------------------|
| `claude-sonnet-4` | `claude-sonnet-4-20250514` |
| `claude-sonnet-4-5` | `claude-sonnet-4-20250514` |
| `claude-sonnet-3-5` | `claude-3-5-sonnet-20241022` |
| `claude-haiku-3-5` | `claude-3-5-haiku-20241022` |

**Unknown models** default to `claude-3-5-haiku-20241022` (fast, cheap).

---

## Environment Variables

| Variable | Purpose | Embeddings | Summarization |
|----------|---------|------------|---------------|
| `VOYAGE_API_KEY` | Voyage AI (Anthropic's partner) | ✓ | - |
| `OPENAI_API_KEY` | OpenAI API | ✓ | ✓ |
| `GEMINI_API_KEY` | Google Gemini API | ✓ | ✓ |
| `ANTHROPIC_API_KEY` | Anthropic API (API key) | - | ✓ |
| `CLAUDE_CODE_OAUTH_TOKEN` | Anthropic API (OAuth token) | - | ✓ |
| `OPENCLAW_CONFIG` | Override OpenClaw config location | - | - |
| `KEEP_STORE_PATH` | Override store location | - | - |

---

## Privacy & Local-First

### What Stays Local

✅ **Vector database** — ChromaDB runs locally
✅ **Embedding cache** — Cached embeddings stored locally
✅ **Configuration** — Stored in `~/.keep/` locally
✅ **Original documents** — Never stored, only summaries and embeddings

### What May Use API

⚠️ **Embeddings** — Local by default (`[local]` install or Ollama), or API (Voyage/OpenAI/Gemini)
⚠️ **Summarization** — Local with Ollama or MLX, or API (Anthropic/OpenAI/Gemini)

For maximum privacy, use Ollama or `pip install 'keep-skill[local]'` with no API keys set.

---

## Use Cases

### Scenario 1: Single API Key (Simplest)

**Setup:**
```bash
pip install keep-skill
export OPENAI_API_KEY=sk-...
keep put "test"
```

**Result:**
- Embeddings: OpenAI (text-embedding-3-small)
- Summarization: OpenAI (gpt-4o-mini)
- Cost: ~$0.0001 per document
- Privacy: Content sent to OpenAI API

---

### Scenario 2: Best Quality (Voyage + Anthropic)

**Setup:**
```bash
pip install keep-skill
export VOYAGE_API_KEY=...
export ANTHROPIC_API_KEY=...
keep put "test"
```

**Result:**
- Embeddings: Voyage (voyage-3.5-lite) — Anthropic's recommended partner
- Summarization: Anthropic (claude-3-haiku)
- Cost: ~$0.0001 per document
- Privacy: Content sent to Voyage and Anthropic APIs

---

### Scenario 3: Pure Local (No API Calls)

**Setup:**
```bash
pip install 'keep-skill[local]'
keep put "test"
```

**Result (Apple Silicon):**
- Embeddings: MLX or sentence-transformers (local)
- Summarization: MLX (local LLM)
- Cost: $0 (all local)
- Privacy: Nothing leaves your machine

**Result (Other platforms):**
- Embeddings: sentence-transformers (local)
- Summarization: Truncate (first 500 chars)
- Cost: $0
- Privacy: Nothing leaves your machine

---

## Customization

### Override Provider After Init

Edit `~/.keep/keep.toml`:

```toml
[summarization]
name = "anthropic"
model = "claude-3-5-haiku-20241022"  # Use Haiku instead of Sonnet
max_tokens = 300  # Longer summaries
```

### Use Different Models for Different Collections

Not yet supported. Roadmap feature for v0.2.

---

## Troubleshooting

### "No embedding provider configured"

**Cause:** No API key set and local models not installed

**Fix (API):**
```bash
export OPENAI_API_KEY=sk-...    # Or VOYAGE_API_KEY, GEMINI_API_KEY
keep put "test"
```

**Fix (Local):**
```bash
pip install 'keep-skill[local]'
keep put "test"
```

---

### "Anthropic/OpenAI/Gemini API key required"

**Cause:** Trying to use a provider without the required API key

**Fix:** Set the appropriate environment variable, or use a different provider.

---

### "Want local-only operation"

**Solution:** Install with `[local]` extra and don't set any API keys.

```bash
pip install 'keep-skill[local]'
keep put "test"              # Uses MLX on Apple Silicon
```

---

## Architecture Notes

### Why Embeddings Stay Local

Embeddings are computed frequently (every document indexed, every query). Using an API would:
- 💸 Cost too much (~$0.0001 per call × thousands of calls)
- 🐌 Be too slow (network latency on every query)
- 🔒 Leak query content (privacy issue)

Local embeddings (sentence-transformers) are:
- ✅ Free
- ✅ Fast (~100ms on M1)
- ✅ Private

### Why Summarization Can Use API

Summaries are computed once per document. Using an API:
- 💸 Reasonable cost (~$0.0001 per document)
- ⚡ Acceptable speed (happens during `update`, not `find`)
- 📝 Better quality than truncation
- 🔄 Original content not stored anyway

---

## Future Enhancements

**Planned for v0.2:**
- [ ] OAuth integration (use OpenClaw's OAuth tokens directly)
- [ ] Per-collection provider config
- [ ] Automatic model upgrades when OpenClaw config changes
- [ ] Batch summarization for cost optimization

---

## Example: Full Workflow

```bash
# 1. Install keep (API SDKs included)
pip install keep-skill

# 2. Set API key(s)
export OPENAI_API_KEY=sk-...    # Simplest: does both embeddings + summarization

# 3. First use auto-initializes
keep put "file://./README.md" -t type=docs
# Store created at ~/.keep/

# 4. Search semantically
keep find "installation instructions" --limit 3

# 5. Check configuration
keep config
# Shows detected providers, store location, etc.
```

---

## Summary

**API providers (recommended for quality):**
- 🧠 Best summarization quality with LLM providers
- 💰 Cost-effective (~$0.0001/document)
- ⚡ Simple setup: just set API key(s)

**Local providers (recommended for privacy):**
- 🏠 Pure local-first (MLX on Apple Silicon)
- 💸 Zero cost
- 🔒 Maximum privacy — nothing leaves your machine

**Recommendation:** Use `OPENAI_API_KEY` for simplest setup (one key does both). Use Voyage + Anthropic for best quality. Use `[local]` install for maximum privacy.
