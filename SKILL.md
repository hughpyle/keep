---
name: assocmem
description: Semantic memory - remember and search documents by meaning, not keywords
metadata: {"openclaw":{"emoji":"🧠","requires":{"bins":["uv"],"anyBins":["python","python3"]},"install":[{"kind":"uv","package":"assocmem[local]","label":"Install assocmem with local models"}],"primaryEnv":"ASSOCMEM_STORE_PATH"}}
---

# 🧠 Associative Memory

*Remember everything. Find by meaning.*

Persistent semantic memory for documents and notes. Index files, remember insights, search by meaning.

## Setup

```bash
uv pip install 'assocmem[local]'
```

Verify: `python -m assocmem init` (creates `.assocmem/` store at repo root)

## Quick Start

1. **Remember something:**
```bash
python -m assocmem remember "User prefers OAuth2 with PKCE for auth" -t topic=auth
```

2. **Index a file:**
```bash
python -m assocmem update "file://$PWD/docs/api.md" -t project=myapp
```

3. **Find by meaning:**
```bash
python -m assocmem find "how does authentication work?" --limit 5
```

4. **Find by tag:**
```bash
python -m assocmem tag topic auth
```

5. **Get specific item:**
```bash
python -m assocmem get "file://$PWD/docs/api.md"
```

## When to Use

- **Before searching files** → `find "error handling"` — may already be indexed
- **After reading important docs** → `update file://...` — remember for later
- **To capture decisions** → `remember "Chose X because Y" -t type=decision`
- **To find related items** → `similar "file://..."` — nearest neighbors

## Commands Reference

| Command | Purpose | Example |
|---------|---------|---------|
| `remember` | Store inline content | `remember "note" -t key=value` |
| `update` | Index document from URI | `update "file:///path" -t key=value` |
| `find` | Semantic search | `find "query" --limit 10` |
| `similar` | Find similar to item | `similar "id" --limit 5` |
| `search` | Full-text search | `search "exact phrase"` |
| `tag` | Query by tag | `tag key value` |
| `get` | Retrieve by ID | `get "id"` |
| `exists` | Check if indexed | `exists "id"` |
| `collections` | List collections | `collections` |
| `init` | Initialize store | `init` |

## Output Format

Add `--json` for structured output:

```bash
python -m assocmem find "auth" --json
```

```json
[
  {
    "id": "file:///path/to/doc.md",
    "summary": "OAuth2 authentication flow with PKCE...",
    "score": 0.847,
    "tags": {"topic": "auth", "_updated": "2026-01-30T14:00:00Z"}
  }
]
```

## Tags

- **Source tags**: You provide via `-t key=value`
- **System tags**: Auto-managed, prefixed with `_` (`_created`, `_updated`, `_source`)

Query system tags: `python -m assocmem tag _updated_date 2026-01-30`

## Store Location

- Default: `.assocmem/` at git repo root
- Override: `ASSOCMEM_STORE_PATH=/path/to/store`
- Add `.assocmem/` to `.gitignore`

## Detailed Guide

See [docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md) for:
- Working session patterns
- Tagging strategies
- Python API reference
- Provider configuration
