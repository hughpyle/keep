# Clawdbot Integration

**Status:** Available in v0.1.0+

---

## Overview

assocmem can automatically integrate with Clawdbot's configured models when both are present. This enables:

- **Unified model configuration** — Configure once in Clawdbot, use everywhere
- **Local-first by default** — Embeddings stay local, summarization can use configured LLM
- **Seamless fallback** — Works standalone without Clawdbot

---

## How It Works

### Detection Priority

When you initialize assocmem, it checks for providers in this order:

1. **Clawdbot integration** (if `~/.clawdbot/clawdbot.json` exists and `ANTHROPIC_API_KEY` set)
2. **MLX** (Apple Silicon local models)
3. **OpenAI** (if `OPENAI_API_KEY` set)
4. **Fallback** (sentence-transformers + truncate)

### What Gets Shared

**From Clawdbot config:**
- Model selection for summarization (e.g., `anthropic/claude-sonnet-4-5`)
- Provider routing (automatically detects Anthropic models)

**Stays local:**
- **Embeddings** always use sentence-transformers (local, privacy-preserving)
- **Store** remains in `.assocmem/` (not shared with Clawdbot)
- **API keys** must be set via environment variables

---

## Setup

### Option 1: Automatic (Recommended)

If you already have Clawdbot configured:

```bash
# 1. Install assocmem with Anthropic support
pip install 'assocmem[clawdbot]'

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Initialize (auto-detects Clawdbot config)
assocmem init
```

**Output:**
```
✓ Store ready: /path/to/workspace/.assocmem
✓ Collections: ['default']

✓ Detected providers:
  Embedding: sentence-transformers (local)
  Summarization: anthropic (claude-sonnet-4)

To customize, edit .assocmem/assocmem.toml
```

### Option 2: Manual Override

Set `CLAWDBOT_CONFIG` to use a different config file:

```bash
export CLAWDBOT_CONFIG=/custom/path/to/clawdbot.json
assocmem init
```

### Option 3: Disable Integration

Don't set `ANTHROPIC_API_KEY`, or remove `~/.clawdbot/clawdbot.json`:

```bash
# Will fall back to MLX (Apple Silicon) or sentence-transformers
assocmem init
```

---

## Configuration Files

### Clawdbot Config Location

Default: `~/.clawdbot/clawdbot.json`

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

### assocmem Config Location

Created at: `.assocmem/assocmem.toml` (workspace root)

**Example (Clawdbot integration active):**
```toml
[store]
version = 1
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

Clawdbot uses short model names. assocmem maps them to actual Anthropic API names:

| Clawdbot Model | Anthropic API Model |
|----------------|---------------------|
| `claude-sonnet-4` | `claude-sonnet-4-20250514` |
| `claude-sonnet-4-5` | `claude-sonnet-4-20250514` |
| `claude-sonnet-3-5` | `claude-3-5-sonnet-20241022` |
| `claude-haiku-3-5` | `claude-3-5-haiku-20241022` |

**Unknown models** default to `claude-3-5-haiku-20241022` (fast, cheap).

---

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API authentication | For Anthropic provider |
| `CLAWDBOT_CONFIG` | Override default config location | Optional |
| `ASSOCMEM_STORE_PATH` | Override store location | Optional |

---

## Privacy & Local-First

### What Stays Local

✅ **Embeddings** — Always computed locally (sentence-transformers)  
✅ **Vector database** — ChromaDB runs locally  
✅ **Embedding cache** — Cached embeddings never leave your machine  
✅ **Configuration** — Stored in `.assocmem/` locally

### What Uses API (Optional)

⚠️ **Summarization** — Only if Anthropic provider configured  
⚠️ **Tagging** — Only if using `anthropic` tagging provider (off by default)

**Original documents are never stored** — Only summaries and embeddings.

---

## Use Cases

### Scenario 1: Clawdbot User (Local-First + Smart Summarization)

**Setup:**
```bash
pip install 'assocmem[clawdbot]'
export ANTHROPIC_API_KEY=sk-ant-...
assocmem init
```

**Result:**
- Embeddings: Local (sentence-transformers)
- Summarization: Anthropic Claude (configured in Clawdbot)
- Cost: ~$0.0001 per document summary
- Privacy: Embeddings local, only summaries sent to API

---

### Scenario 2: Pure Local (No API Calls)

**Setup:**
```bash
pip install 'assocmem[local]'  # No API dependencies
assocmem init
```

**Result (Apple Silicon):**
- Embeddings: Local (sentence-transformers)
- Summarization: Local (MLX + Llama 3.2)
- Cost: $0 (all local)
- Privacy: Nothing leaves your machine

**Result (Other platforms):**
- Embeddings: Local (sentence-transformers)
- Summarization: Truncate (first 500 chars)
- Cost: $0
- Privacy: Nothing leaves your machine

---

### Scenario 3: OpenAI User (No Clawdbot)

**Setup:**
```bash
pip install 'assocmem[openai]'
export OPENAI_API_KEY=sk-...
assocmem init
```

**Result:**
- Embeddings: Local (sentence-transformers)
- Summarization: OpenAI (gpt-4o-mini)
- Cost: ~$0.0001 per document summary
- Privacy: Embeddings local, only summaries sent to API

---

## Customization

### Override Provider After Init

Edit `.assocmem/assocmem.toml`:

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

### "Clawdbot config found but Anthropic provider not used"

**Cause:** `ANTHROPIC_API_KEY` not set

**Fix:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
rm -rf .assocmem  # Delete old config
assocmem init  # Re-initialize
```

---

### "AnthropicSummarization requires 'anthropic' library"

**Cause:** Package not installed

**Fix:**
```bash
pip install 'assocmem[clawdbot]'
```

---

### "Want to use Clawdbot integration but don't have API key"

**Solution:** Use local-first mode. Clawdbot config is ignored if no API key present.

```bash
pip install 'assocmem[local]'  # MLX on Apple Silicon
assocmem init
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
- [ ] OAuth integration (use Clawdbot's OAuth tokens directly)
- [ ] Per-collection provider config
- [ ] Automatic model upgrades when Clawdbot config changes
- [ ] Batch summarization for cost optimization

---

## Example: Full Workflow

```bash
# 1. Install with Clawdbot integration
pip install 'assocmem[clawdbot]'

# 2. Set API key (from Anthropic console)
export ANTHROPIC_API_KEY=sk-ant-api03-...

# 3. Initialize (detects Clawdbot config automatically)
assocmem init
# ✓ Store ready: /Users/hugh/clawd/.assocmem
# ✓ Detected providers:
#   Embedding: sentence-transformers (local)
#   Summarization: anthropic (claude-sonnet-4)

# 4. Index a document
assocmem update "file://./README.md" -t type=docs

# 5. Search semantically
assocmem find "installation instructions" --limit 3

# 6. Verify costs are reasonable
# Claude Haiku: ~$0.0001 per summary
# 1000 documents = ~$0.10 total
```

---

## Summary

**With Clawdbot integration:**
- 🧠 Best of both worlds: local embeddings + smart summarization
- 🔄 Unified configuration (DRY principle)
- 💰 Cost-effective ($0.0001/document vs $0.001 for OpenAI embeddings)
- 🔒 Privacy-preserving (embeddings + queries stay local)

**Without Clawdbot:**
- 🏠 Pure local-first (MLX on Apple Silicon)
- 💸 Zero cost
- 🔒 Maximum privacy

**Recommendation:** Use Clawdbot integration if you already have it configured. Otherwise, local-first mode is excellent for privacy and zero cost.
