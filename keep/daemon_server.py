"""HTTP query server for the keep daemon.

Exposes endpoints on localhost for all keep operations.
Core CRUD via notes endpoints, search via /search, prompt rendering
via /prompt, and extensible operations via /flow.

Usage::

    from keep.daemon_server import DaemonServer

    server = DaemonServer(keeper, port=5337)
    actual_port = server.start()
    # ... daemon work loop ...
    server.stop()
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

if TYPE_CHECKING:
    from .api import Keeper

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5337


def _item_to_dict(item) -> dict:
    """Convert Item to JSON dict matching RemoteKeeper._to_item() expectations."""
    d: dict[str, Any] = {
        "id": item.id,
        "summary": item.summary,
        "tags": item.tags or {},
    }
    if item.score is not None:
        d["score"] = item.score
    if item.tags:
        if "_created" in item.tags:
            d["created_at"] = item.tags["_created"]
        if "_updated" in item.tags:
            d["updated_at"] = item.tags["_updated"]
    return d


def _items_response(items) -> dict:
    """Wrap a list of Items in the response envelope."""
    return {"notes": [_item_to_dict(i) for i in items]}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_ROUTES: list[tuple[str, str, str]] = [
    ("GET",    r"^/v1/health$",                       "_handle_health"),
    ("POST",   r"^/v1/search$",                       "_handle_find"),
    ("POST",   r"^/v1/flow$",                         "_handle_flow"),
    ("POST",   r"^/v1/analyze$",                      "_handle_analyze"),
    ("POST",   r"^/v1/notes$",                        "_handle_put"),
    ("GET",    r"^/v1/notes/(?P<id>.+)/context$",     "_handle_get_context"),
    ("PATCH",  r"^/v1/notes/(?P<id>.+)/tags$",        "_handle_tag"),
    ("DELETE", r"^/v1/notes/(?P<id>.+)$",             "_handle_delete"),
    ("GET",    r"^/v1/notes/(?P<id>.+)$",             "_handle_get"),
]

_COMPILED_ROUTES = [
    (method, re.compile(pattern), handler)
    for method, pattern, handler in _ROUTES
]


class DaemonRequestHandler(BaseHTTPRequestHandler):
    """Routes requests to Keeper methods."""

    keeper: "Keeper"

    def log_message(self, format, *args):
        logger.debug("HTTP %s", format % args)

    def _dispatch(self, method: str):
        path = urlparse(self.path).path
        for route_method, pattern, handler_name in _COMPILED_ROUTES:
            if route_method != method:
                continue
            m = pattern.match(path)
            if m:
                groups = {k: unquote(v) for k, v in m.groupdict().items()}
                try:
                    getattr(self, handler_name)(groups)
                except Exception as e:
                    logger.warning("Handler %s error: %s", handler_name, e, exc_info=True)
                    self._json(500, {"error": str(e)})
                return
        self._json(404, {"error": f"Not found: {method} {path}"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # --- Helpers ---

    def _json(self, status: int, data: Any):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    # --- Handlers ---

    _cached_version: str = ""

    def _handle_health(self, groups: dict):
        if not DaemonRequestHandler._cached_version:
            from importlib.metadata import version
            try:
                DaemonRequestHandler._cached_version = version("keep-skill")
            except Exception:
                DaemonRequestHandler._cached_version = "unknown"

        kp = self.keeper
        config = kp._config

        # Embedding provider status
        embedding = None
        if config.embedding:
            embedding = config.embedding.name
            model = config.embedding.params.get("model", "")
            if model:
                embedding = f"{embedding}/{model}"

        # Summarization provider status
        summarization = None
        if config.summarization:
            summarization = config.summarization.name

        # Warnings
        warnings = []
        if not config.embedding:
            warnings.append("no embedding provider configured")
        if not config.summarization:
            warnings.append("no summarization provider configured")

        # Needs setup: no config file or no embedding provider
        needs_setup = config.embedding is None

        self._json(200, {
            "status": "ok",
            "pid": os.getpid(),
            "version": DaemonRequestHandler._cached_version,
            "store": str(kp._store_path),
            "embedding": embedding,
            "summarization": summarization,
            "item_count": kp.count(),
            "needs_setup": needs_setup,
            "warnings": warnings,
        })

    def _handle_get(self, groups: dict):
        item = self.keeper.get(groups["id"])
        if item is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, _item_to_dict(item))

    def _handle_get_context(self, groups: dict):
        qs = urlparse(self.path).query
        params = parse_qs(qs, keep_blank_values=True)

        def _int(key, default):
            v = params.get(key)
            if v:
                try:
                    return int(v[0])
                except (ValueError, IndexError):
                    pass
            return default

        def _bool(key, default=True):
            v = params.get(key)
            if v:
                return v[0].lower() in ("true", "1", "yes")
            return default

        id = groups["id"]
        # Auto-create "now" document if missing (matches old CLI behavior)
        if id == "now" and self.keeper.get(id) is None:
            self.keeper.get_now()

        ctx = self.keeper.get_context(
            id,
            version=_int("version", None),
            similar_limit=_int("similar_limit", 3),
            meta_limit=_int("meta_limit", 3),
            parts_limit=_int("parts_limit", 10),
            edges_limit=_int("edges_limit", 5),
            versions_limit=_int("versions_limit", 3),
            include_similar=_bool("include_similar"),
            include_meta=_bool("include_meta"),
            include_parts=_bool("include_parts"),
            include_versions=_bool("include_versions"),
        )
        if ctx is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, ctx.to_dict())

    def _handle_put(self, groups: dict):
        body = self._read_body()
        item = self.keeper.put(
            content=body.get("content"),
            uri=body.get("uri"),
            id=body.get("id"),
            summary=body.get("summary"),
            tags=body.get("tags"),
            created_at=body.get("created_at"),
            force=body.get("force", False),
        )
        self._json(200, _item_to_dict(item))

    def _handle_delete(self, groups: dict):
        deleted = self.keeper.delete(groups["id"])
        self._json(200, {"deleted": deleted})

    def _handle_find(self, groups: dict):
        body = self._read_body()
        results = self.keeper.find(
            query=body.get("query"),
            tags=body.get("tags"),
            similar_to=body.get("similar_to"),
            limit=body.get("limit", 10),
            since=body.get("since"),
            until=body.get("until"),
            include_self=body.get("include_self", False),
            include_hidden=body.get("include_hidden", False),
            deep=body.get("deep", False),
            scope=body.get("scope"),
        )
        resp = _items_response(results)
        if hasattr(results, "deep_groups") and results.deep_groups:
            resp["deep_groups"] = [
                {"id": pid, "items": [_item_to_dict(i) for i in items]}
                for pid, items in results.deep_groups.items()
            ]
        self._json(200, resp)

    def _handle_tag(self, groups: dict):
        body = self._read_body()
        set_tags = body.get("set", {})
        remove_keys = body.get("remove", [])
        tags = dict(set_tags)
        for k in remove_keys:
            tags[k] = ""
        item = self.keeper.tag(groups["id"], tags)
        if item is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, _item_to_dict(item))

    def _handle_flow(self, groups: dict):
        body = self._read_body()
        result = self.keeper.run_flow_command(
            state=body.get("state", ""),
            params=body.get("params", {}),
            budget=body.get("budget", 5),
            cursor_token=body.get("cursor_token") or body.get("cursor"),
            state_doc_yaml=body.get("state_doc_yaml"),
        )
        self._json(200, {
            "status": result.status,
            "bindings": result.bindings,
            "data": result.data,
            "ticks": result.ticks,
            "history": result.history,
            "cursor": result.cursor,
        })

    def _handle_analyze(self, groups: dict):
        body = self._read_body()
        id = body.get("id", "")
        if not id:
            self._json(400, {"error": "id is required"})
            return

        tags = body.get("tags")
        force = body.get("force", False)
        foreground = body.get("foreground", False)

        if not foreground:
            try:
                enqueued = self.keeper.enqueue_analyze(id, tags=tags, force=force)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {
                "id": id,
                "queued": enqueued,
                "skipped": not enqueued,
            })
        else:
            try:
                parts = self.keeper.analyze(id, tags=tags, force=force)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {
                "id": id,
                "parts": [
                    {
                        "part_num": p.part_num,
                        "summary": p.summary[:100],
                        "tags": {k: v for k, v in p.tags.items() if not k.startswith("_")},
                    }
                    for p in (parts or [])
                ],
            })


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

class DaemonServer:
    """HTTP server lifecycle for the daemon."""

    def __init__(self, keeper: "Keeper", port: int = DEFAULT_PORT):
        self._keeper = keeper
        self._preferred_port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> int:
        """Start the HTTP server. Returns the actual bound port."""
        DaemonRequestHandler.keeper = self._keeper
        try:
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", self._preferred_port), DaemonRequestHandler)
        except OSError:
            logger.info("Port %d in use, using OS-assigned port", self._preferred_port)
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", 0), DaemonRequestHandler)

        port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="daemon-http")
        self._thread.start()
        logger.info("Query server listening on 127.0.0.1:%d", port)
        return port

    def stop(self):
        """Shut down the HTTP server."""
        if self._server:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Query server stopped")
