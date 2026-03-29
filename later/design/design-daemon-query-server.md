# Daemon as Query Server

## Problem

Every CLI invocation pays ~3-4s startup: Python imports (~400ms), embedding model load (~2-3s), ChromaDB init (~500ms). The daemon already runs persistently and owns all these resources, but CLI commands don't talk to it.

## Design

The daemon adds an HTTP server on `127.0.0.1`. CLI commands become HTTP clients, falling back to direct Keeper only when the daemon isn't running.

```
keep find "X" → connect localhost:PORT → POST /v1/search → result → exit
                                                  ↑
daemon (persistent): Keeper + models + ChromaDB + cache
```

## HTTP Server

**`ThreadingHTTPServer` (stdlib)** — no new dependencies. Each request gets a thread; Keeper is already thread-safe (SQLite RLock, ChromaDB state lock). Server runs in a daemonic thread alongside the existing background work loop.

### Port selection

```
1. KEEP_DAEMON_PORT env var
2. config.daemon.port
3. Default 5337; EADDRINUSE → port 0 (OS-assigned)
```

Actual port written to `{store_path}/.daemon.port`. Clients always read this file. Multiple stores = multiple daemons, each with its own port file. Deleted on shutdown.

### Startup

In the daemon branch, after Keeper init:

```python
server = ThreadingHTTPServer(("127.0.0.1", preferred_port), DaemonHandler)
DaemonHandler.keeper = kp
(store_path / ".daemon.port").write_text(str(server.server_address[1]))
threading.Thread(target=server.serve_forever, daemon=True).start()
```

## Endpoints

Reuse the RemoteKeeper REST API from `remote.py`. The daemon speaks the same protocol as the hosted service.

| Method | Endpoint |
|--------|----------|
| `find()` | `POST /v1/search` |
| `get()` | `GET /v1/notes/{id}` |
| `get_context()` | `GET /v1/notes/{id}/context` |
| `get_now()` | `GET /v1/now` |
| `list_items()` | `GET /v1/notes` |
| `list_tags()` | `GET /v1/tags` |
| `put()` | `POST /v1/notes` |
| `tag()` | `PATCH /v1/notes/{id}/tags` |
| `delete()` | `DELETE /v1/notes/{id}` |
| `run_flow_command()` | `POST /v1/flow` |
| health | `GET /v1/health` |
| perf stats | `GET /v1/perf` |

`get_context()` is a new combined endpoint — returns the full `ItemContext` in one call (currently RemoteKeeper assembles it from 5+ calls).

## CLI Client

```python
def _get_keeper_or_client(store_path, config) -> KeeperProtocol:
    port_file = store_path / ".daemon.port"
    if port_file.exists():
        try:
            port = int(port_file.read_text().strip())
            client = RemoteKeeper(api_url=f"http://127.0.0.1:{port}", api_key="", config=config)
            client._get("/v1/health")
            return client
        except Exception:
            pass
    return Keeper(store_path=store_path, config=config)
```

Both `Keeper` and `RemoteKeeper` implement `KeeperProtocol`. CLI code works with the protocol — no changes to individual commands. All read and write commands go through daemon when available. Only `keep pending`, `keep config`, and `keep setup` stay direct.

## Concurrency

- SQLite WAL: concurrent readers + one writer
- ChromaDB HNSW: reads don't block writes
- Daemon processes one background item at a time; HTTP requests are concurrent reads
- Embedding model contention (CLI find + background embed) blocks one until the other finishes — already the case today via ModelLock, but now shared rather than duplicated

## Scope

New file: `keep/daemon_server.py` (HTTP handler, endpoint routing). Changes: `cli.py` (start server in daemon branch, try daemon client in commands), `_background_processing.py` (port file cleanup).

**Auto-start:** CLI commands auto-start the daemon on first use if not running (`daemon.auto_start` config flag, default true). Subsequent commands hit the warm daemon. Daemon exits after idle timeout (existing drain logic).

**MCP as daemon client:** MCP plugin becomes a thin stdio→HTTP bridge — no Python runtime, no model loading. Endpoint surface is already compatible (same as RemoteKeeper).

**ModelLock elimination:** With daemon as the single model owner, CLI stops loading models. `ModelLock` and cross-process epoch coordination become unnecessary — remove them.

## Decisions

**No fallback to direct mode.** CLI requires the daemon. If it's not running, auto-start it; if it's unreachable, fail with a clear error. No dual code path, no silent degradation. This keeps the client thin and eliminates the need for model loading, ModelLock, and ChromaDB init in the CLI process entirely.

**No auth.** Localhost-only, same trust model as SQLite/ChromaDB files (filesystem permissions).
