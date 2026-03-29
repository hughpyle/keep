# Thin CLI

## Goal

The CLI becomes a pure display layer: parse args → one HTTP call → render JSON → exit. No Python imports of keep internals, no database, no models. Startup is ~50ms. Could be reimplemented in TypeScript or any language.

## Server endpoints (8)

```
GET    /v1/health
GET    /v1/notes/{id}
GET    /v1/notes/{id}/context?similar_limit=3&meta_limit=3&...
POST   /v1/notes
DELETE /v1/notes/{id}
POST   /v1/search
PATCH  /v1/notes/{id}/tags
POST   /v1/flow
```

The `/context` endpoint returns everything needed for `keep get` display in one round-trip: item + similar + meta + edges + parts + versions, trimmed by server-side limits.

## CLI command → HTTP mapping

| Command | HTTP call | Notes |
|---|---|---|
| `keep` (no args) | `GET /v1/notes/now/context` | |
| `keep get <id>` | `GET /v1/notes/{id}/context` | |
| `keep get <id> -V3` | `GET /v1/notes/{id}/context?version=3` | |
| `keep find <query>` | `POST /v1/search` | |
| `keep find --similar <id>` | `POST /v1/search {similar_to: id}` | |
| `keep put <content>` | `POST /v1/notes` | |
| `keep put <file>` | `POST /v1/notes {uri: "file://..."}` | |
| `keep tag <id> k=v` | `PATCH /v1/notes/{id}/tags` | |
| `keep del <id>` | `DELETE /v1/notes/{id}` | |
| `keep now <content>` | `POST /v1/notes {id: "now", content: ...}` | |
| `keep list` | `POST /v1/search` with tag/prefix filters | Or add `GET /v1/notes` back |
| `keep move <name>` | `POST /v1/flow {state: "move", ...}` | |
| `keep prompt <name>` | `POST /v1/flow {state: "prompt", ...}` | |
| `keep reflect <text>` | `POST /v1/flow {state: "prompt", params: {name: "reflect", ...}}` | |
| `keep analyze <id>` | `POST /v1/flow {state: "after-write", ...}` | |
| `keep export` | `POST /v1/flow {state: "export"}` | Or stream endpoint |
| `keep pending` | Direct local (manages daemon) | |

## What the CLI does

1. **Parse args** (typer or argparse — lightweight)
2. **Resolve daemon port** (read `.daemon.port` file, auto-start if needed)
3. **One HTTP call** (stdlib `http.client` or `urllib` — no httpx dependency)
4. **Render JSON response** as terminal output (YAML frontmatter, tables, colors)
5. **Exit**

## What the CLI does NOT do

- Import `keep.api`, `keep.store`, `keep.document_store`, etc.
- Load embedding models or ChromaDB
- Parse or evaluate state docs
- Count tokens or trim context
- Resolve meta-docs, parts, or versions
- Manage the work queue (except `keep pending` which is `_force_local`)

## What the server does (that the CLI used to do)

- Context assembly: similar, meta, parts, edges, versions — all assembled by the `get_context()` flow
- Token-budget trimming: prompt rendering respects budgets server-side
- Part windowing: `_focus_part` applied server-side
- Version navigation: prev/next computed server-side
- Keyword passage extraction: `_find_best_passage` runs server-side in the find path
- Deep group assembly: edge-following and group building in find

## Rendering

The CLI renders JSON responses as formatted terminal output. Key renderers:

- **Context renderer**: Takes `ItemContext` JSON → YAML frontmatter + similar/meta/edges/parts/prev sections
- **Find renderer**: Takes search results JSON → scored item list with deep groups
- **Put renderer**: Takes item JSON → confirmation + brief context

These are ~200-300 lines of pure formatting code. No keep domain logic.

## Implementation

### Step 1: Add `/context` endpoint back to daemon_server.py

Already had it, was removed in the simplification. Add it back — it's a thin wrapper around `keeper.get_context()` returning `ItemContext.to_dict()`.

### Step 2: New thin CLI module — `keep/thin_cli.py`

New entry point that imports ONLY stdlib + rendering code. No `keep.api`.

```python
#!/usr/bin/env python3
"""Thin CLI — talks to daemon via HTTP, renders results."""

import json
import sys
from http.client import HTTPConnection
from pathlib import Path

def main():
    port = _read_port()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "get" or cmd == "":
        id = sys.argv[2] if cmd == "get" else "now"
        resp = _get(port, f"/v1/notes/{_q(id)}/context")
        _render_context(resp)
    elif cmd == "find":
        resp = _post(port, "/v1/search", {"query": sys.argv[2]})
        _render_find(resp)
    elif cmd == "put":
        resp = _post(port, "/v1/notes", {"content": sys.argv[2]})
        _render_put(resp)
    # ... etc
```

### Step 3: Migrate commands one at a time

Move each command from `cli.py` to the thin CLI:
1. `keep` (no args) / `keep get` — highest traffic, most impact
2. `keep find` — second most common
3. `keep put` — third
4. `keep list`, `keep tag`, `keep del` — simple
5. `keep now`, `keep move`, `keep prompt`, `keep reflect` — flow-based
6. `keep pending`, `keep export`, `keep import` — stay local

### Step 4: Remove old CLI code

Once all commands are migrated, the old `cli.py` shrinks to just `pending_cmd` (daemon management) and the thin CLI handles everything else.

## Decisions

- **Arg parsing**: typer (already a dependency)
- **`keep list`**: use `POST /v1/search` with tag/prefix filters — no separate list endpoint
- **`keep export`**: buffer in memory — no streaming endpoint needed
