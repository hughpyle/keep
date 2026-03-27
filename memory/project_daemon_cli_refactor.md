---
name: Daemon CLI refactor
description: Status of the thin CLI refactor — entry point switched, most commands via HTTP, some still delegated
type: project
---

## Thin CLI refactor status (2026-03-26)

Entry point switched: `pyproject.toml` and `__main__.py` now point to `keep.thin_cli:main`.

### Commands fully via HTTP (no keep internals):
- `get`, `find`, `put`, `tag`, `del`, `now`, `list`, `move` — via existing daemon endpoints
- `prompt`, `reflect` — via new `POST /v1/prompt` and `GET /v1/prompts` endpoints
- `analyze` — via new `POST /v1/analyze` endpoint
- `flow` — via existing `POST /v1/flow` endpoint
- `edit` — GET item from daemon, local editor, PUT back via daemon
- `help` — lightweight `keep.help` module (static docs, no heavy imports)

### Commands still delegated to full CLI:
- `pending` — manages the daemon process itself (stays local by design)
- `config` — needs access to config files, tool paths, setup wizard
- `doctor` — probes providers, stores, ChromaDB directly
- `data export/import` — bulk file I/O
- `mcp` — starts separate MCP stdio server
- `validate` — deprecated
- `put /directory/` — directory mode with recursion/watch/exclude

### Server endpoints added:
- `GET /v1/prompts` — list available prompt docs
- `POST /v1/prompt` — render + expand prompt server-side
- `POST /v1/analyze` — enqueue or foreground analyze

### Known gaps:
- `RemoteKeeper` missing `_load_ignore_patterns` — directory put via daemon fails (pre-existing)
- Daemon must be restarted to pick up new code (auto-create "now" in context endpoint)

**Why:** Fast CLI startup (~50ms), decoupled from Python/ML model loading. Could be reimplemented in any language.
**How to apply:** When adding new CLI commands, add HTTP endpoint first, then thin CLI command.
