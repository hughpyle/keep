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
import hmac
import ipaddress
import logging
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

if TYPE_CHECKING:
    from .api import Keeper

logger = logging.getLogger(__name__)

from .const import DAEMON_PORT
from .flow_client import (
    delete_item as flow_delete_item,
    find_items as flow_find_items,
    get_item as flow_get_item,
    put_item as flow_put_item,
    tag_item as flow_tag_item,
)
from .markdown_mirrors import (
    add_markdown_mirror,
    clear_sync_outbox,
    record_markdown_mirror_export_success,
    remove_markdown_mirror,
    run_markdown_export_once,
    validate_markdown_mirror,
)
from .markdown_export import _get_export_bundle
from . import markdown_export as _markdown_export
from .watches import add_watch, remove_watch


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
    ("GET",    r"^/v1/ready$",                        "_handle_ready"),
    ("GET",    r"^/v1/health$",                       "_handle_health"),
    ("GET",    r"^/v1/export/changes$",              "_handle_export_changes"),
    ("GET",    r"^/v1/export/bundles/(?P<id>.+)$",    "_handle_export_bundle"),
    ("GET",    r"^/v1/export$",                       "_handle_export"),
    ("POST",   r"^/v1/search$",                       "_handle_find"),
    ("POST",   r"^/v1/flow$",                         "_handle_flow"),
    ("POST",   r"^/v1/analyze$",                      "_handle_analyze"),
    ("POST",   r"^/v1/notes$",                        "_handle_put"),
    ("GET",    r"^/v1/notes/(?P<id>.+)/context$",     "_handle_get_context"),
    ("PATCH",  r"^/v1/notes/(?P<id>.+)/tags$",        "_handle_tag"),
    ("DELETE", r"^/v1/notes/(?P<id>.+)$",             "_handle_delete"),
    ("GET",    r"^/v1/notes/(?P<id>.+)$",             "_handle_get"),
    ("POST",   r"^/v1/admin/reset-system-docs$",     "_handle_reset_system_docs"),
    ("POST",   r"^/v1/admin/markdown-export$",       "_handle_markdown_export"),
]

_COMPILED_ROUTES = [
    (method, re.compile(pattern), handler)
    for method, pattern, handler in _ROUTES
]
from .api import _MAX_EXPORT_CHANGES_LIMIT


def _normalize_host(value: str) -> str:
    host = (value or "").strip().lower()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif ":" in host:
        host = host.split(":", 1)[0]
    return host


def _is_loopback_host(value: str) -> bool:
    host = _normalize_host(value)
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_wildcard_bind_host(value: str) -> bool:
    return _normalize_host(value) in ("", "0.0.0.0", "::")


def _allowed_hosts_for_mode(bind_host: str, advertised_url: str | None) -> set[str]:
    bind_host = _normalize_host(bind_host)
    loopbacks = {"", "127.0.0.1", "localhost", "::1"}
    if _is_loopback_host(bind_host):
        return loopbacks

    allowed = set(loopbacks)
    if bind_host and not _is_wildcard_bind_host(bind_host):
        allowed.add(bind_host)
    if advertised_url:
        advertised_host = _normalize_host(urlparse(advertised_url).hostname or "")
        if advertised_host:
            allowed.add(advertised_host)
    return allowed


def _validate_remote_bind_policy(
    bind_host: str,
    advertised_url: str | None,
    *,
    trusted_proxy: bool,
) -> None:
    if _is_loopback_host(bind_host):
        return
    if not trusted_proxy:
        raise ValueError(
            "non-loopback daemon bind requires explicit trusted proxy mode; "
            "pass --trusted-proxy or set KEEP_DAEMON_TRUSTED_PROXY=1"
        )
    if _is_wildcard_bind_host(bind_host) and not advertised_url:
        raise ValueError(
            "wildcard daemon bind requires --advertised-url to preserve "
            "Host-header protection"
        )


class DaemonRequestHandler(BaseHTTPRequestHandler):
    """Routes requests to Keeper methods."""

    keeper: "Keeper"
    export_keeper: Any = None
    auth_token: str = ""
    allowed_hosts: set[str] = {"", "127.0.0.1", "localhost", "::1"}
    bind_host: str = "127.0.0.1"
    advertised_url: str | None = None

    def log_message(self, format, *args):
        logger.debug("HTTP %s", format % args)

    def _dispatch(self, method: str):
        # Host header check — reject DNS rebinding attempts
        host = _normalize_host(self.headers.get("Host") or "")
        if host not in self.allowed_hosts:
            self._json(403, {"error": "forbidden"})
            return

        # Auth token check
        if DaemonRequestHandler.auth_token:
            provided = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(provided, DaemonRequestHandler.auth_token):
                self._json(401, {"error": "unauthorized"})
                return

        from .tracing import get_tracer
        from opentelemetry.propagate import extract
        from .shutdown import is_shutting_down

        if is_shutting_down():
            self._json(503, {"error": "shutting down"})
            return

        path = urlparse(self.path).path
        for route_method, pattern, handler_name in _COMPILED_ROUTES:
            if route_method != method:
                continue
            m = pattern.match(path)
            if m:
                groups = {k: unquote(v) for k, v in m.groupdict().items()}
                # Extract trace context from incoming headers (CLI → daemon)
                ctx = extract(dict(self.headers))
                tracer = get_tracer("http")
                with tracer.start_as_current_span(
                    f"{method} {path}",
                    context=ctx,
                    attributes={"http.method": method, "http.path": path},
                ):
                    try:
                        getattr(self, handler_name)(groups)
                    except ValueError as e:
                        logger.warning("Handler %s rejected request: %s", handler_name, e)
                        self._json(400, {"error": str(e)})
                    except Exception as e:
                        logger.warning("Handler %s error: %s", handler_name, e, exc_info=True)
                        self._json(500, {"error": "internal server error"})
                return
        self._json(404, {"error": "not found"})

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

    def _stream_ndjson(self, status: int, rows) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for row in rows:
                line = json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")
                self.wfile.write(line + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("NDJSON stream closed by client")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    # --- Handlers ---

    _cached_version: str = ""

    def _daemon_status(self, *, include_item_count: bool) -> dict[str, Any]:
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

        data: dict[str, Any] = {
            "status": "ok",
            "pid": os.getpid(),
            "version": DaemonRequestHandler._cached_version,
            "store": str(kp._store_path),
            "embedding": embedding,
            "summarization": summarization,
            "needs_setup": needs_setup,
            "warnings": warnings,
            "capabilities": {
                "api_version": 1,
                "export_snapshot": True,
                "export_stream_ndjson": True,
                "export_bundle": True,
                "export_changes": True,
                "remote_incremental_markdown_sync": True,
            },
            "network": {
                "mode": "local" if _is_loopback_host(self.bind_host) else "remote",
                "bind_host": self.bind_host,
                "advertised_url": self.advertised_url,
            },
        }
        if include_item_count:
            try:
                data["item_count"] = kp.count()
            except Exception as exc:
                logger.warning("Health diagnostics count failed: %s", exc, exc_info=True)
                data["item_count"] = None
                warnings.append("item count unavailable")
        return data

    def _handle_ready(self, groups: dict):
        self._json(200, self._daemon_status(include_item_count=False))

    def _handle_health(self, groups: dict):
        self._json(200, self._daemon_status(include_item_count=True))

    def _handle_export(self, groups: dict):
        export_keeper = self.export_keeper or self.keeper
        qs = urlparse(self.path).query
        params = parse_qs(qs, keep_blank_values=True)
        raw_include_system = params.get("include_system", ["true"])[0].strip().lower()
        include_system = raw_include_system not in ("false", "0", "no")
        raw_stream = params.get("stream", [""])[0].strip().lower()
        if raw_stream in ("ndjson", "jsonl", "stream", "true", "1", "yes"):
            self._stream_ndjson(
                200,
                export_keeper.export_iter(include_system=include_system),
            )
            return
        self._json(200, export_keeper.export_data(include_system=include_system))

    def _handle_export_changes(self, groups: dict):
        export_keeper = self.export_keeper or self.keeper
        qs = urlparse(self.path).query
        params = parse_qs(qs, keep_blank_values=True)
        raw_limit = params.get("limit", ["1000"])[0].strip()
        try:
            limit = max(0, min(int(raw_limit), _MAX_EXPORT_CHANGES_LIMIT))
        except ValueError:
            self._json(400, {"error": f"invalid limit: {raw_limit}"})
            return
        try:
            payload = export_keeper.export_changes(
                cursor=params.get("cursor", ["0"])[0].strip() or "0",
                limit=limit,
            )
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, payload)

    def _handle_export_bundle(self, groups: dict):
        export_keeper = self.export_keeper or self.keeper
        qs = urlparse(self.path).query
        params = parse_qs(qs, keep_blank_values=True)

        def _bool(key: str, default: bool = True) -> bool:
            raw = params.get(key, [str(default).lower()])[0].strip().lower()
            return raw not in ("false", "0", "no")

        if _markdown_export.supports_local_markdown_export_graph(export_keeper):
            bundle = _get_export_bundle(
                export_keeper,
                groups["id"],
                include_system=_bool("include_system", True),
                include_parts=_bool("include_parts", True),
                include_versions=_bool("include_versions", True),
            )
        else:
            bundle = export_keeper.export_bundle(
                groups["id"],
                include_system=_bool("include_system", True),
                include_parts=_bool("include_parts", True),
                include_versions=_bool("include_versions", True),
            )
        if bundle is None:
            self._json(404, {"error": "not found"})
            return
        self._json(200, bundle)

    def _handle_get(self, groups: dict):
        item = flow_get_item(self.keeper, groups["id"])
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
        watch = body.get("watch", False)
        unwatch = body.get("unwatch", False)
        watch_kind = body.get("watch_kind", "file")

        # Directory watches do not correspond to a single document: the CLI
        # walked the directory and posted each child file individually before
        # this request. Skip flow_put_item entirely and go straight to watch
        # registration, otherwise the put would blow up on "Not a file".
        if (watch or unwatch) and watch_kind == "directory":
            # Directory watch entries store a bare filesystem path, not a URI.
            from .types import file_uri_to_path
            raw_uri = body.get("uri") or ""
            source = file_uri_to_path(raw_uri) if raw_uri.startswith("file://") else raw_uri
            if not source:
                self._json(400, {"error": "directory watch requires a uri"})
                return
            resp: dict = {}
            if unwatch:
                resp["unwatch"] = remove_watch(self.keeper, source)
            else:
                entry = add_watch(
                    self.keeper, source, watch_kind,
                    tags=body.get("tags") or {},
                    recurse=body.get("recurse", False),
                    exclude=body.get("exclude") or [],
                    interval=body.get("interval", "PT30S"),
                    max_watches=self.keeper.config.max_watches,
                )
                resp["watch"] = {"source": entry.source, "interval": entry.interval}
            self._json(200, resp)
            return

        item = flow_put_item(
            self.keeper,
            content=body.get("content"),
            uri=body.get("uri"),
            id=body.get("id"),
            summary=body.get("summary"),
            tags=body.get("tags"),
            created_at=body.get("created_at"),
            force=body.get("force", False),
        )
        resp = _item_to_dict(item)

        # Watch management (after successful put) — file and url kinds only
        if watch or unwatch:
            source = body.get("uri") or item.id
            if unwatch:
                removed = remove_watch(self.keeper, source)
                resp["unwatch"] = removed
            else:
                entry = add_watch(
                    self.keeper, source, watch_kind,
                    tags=body.get("tags") or {},
                    recurse=body.get("recurse", False),
                    exclude=body.get("exclude") or [],
                    interval=body.get("interval", "PT30S"),
                    max_watches=self.keeper.config.max_watches,
                )
                resp["watch"] = {"source": entry.source, "interval": entry.interval}

        self._json(200, resp)

    def _handle_delete(self, groups: dict):
        deleted = flow_delete_item(self.keeper, groups["id"])
        self._json(200, {"deleted": deleted})

    def _handle_markdown_export(self, groups: dict):
        body = self._read_body()
        export_keeper = self.export_keeper or self.keeper
        if body.get("list"):
            from .markdown_mirrors import list_markdown_mirrors

            entries = list_markdown_mirrors(self.keeper)
            self._json(200, {
                "mirrors": [
                    {
                        "root": entry.root,
                        "enabled": entry.enabled,
                        "include_system": entry.include_system,
                        "include_parts": entry.include_parts,
                        "include_versions": entry.include_versions,
                        "interval": entry.interval,
                        "added_at": entry.added_at,
                        "pending_since": entry.pending_since,
                        "last_run": entry.last_run,
                        "last_error": entry.last_error,
                    }
                    for entry in entries
                ],
            })
            return

        root = body.get("root") or body.get("output")
        if not root:
            self._json(400, {"error": "markdown export requires a root directory"})
            return

        include_system = bool(body.get("include_system", False))
        include_parts = bool(body.get("include_parts", False))
        include_versions = bool(body.get("include_versions", False))
        sync = bool(body.get("sync", False))
        stop = bool(body.get("stop", False))
        register_only = bool(body.get("register_only", False))
        validate_only = bool(body.get("validate_only", False))
        baseline_complete = bool(body.get("baseline_complete", False))
        interval = str(body.get("interval") or "PT30S")
        source_cursor = str(body.get("source_cursor") or "")

        try:
            if stop:
                removed = remove_markdown_mirror(self.keeper, root)
                self._json(200, {"stopped": removed, "root": str(root)})
                return

            if validate_only:
                resolved_root, _entries = validate_markdown_mirror(
                    self.keeper,
                    root,
                    interval=interval,
                )
                self._json(200, {
                    "validated": True,
                    "root": resolved_root,
                })
                return

            if sync:
                entry = add_markdown_mirror(
                    self.keeper,
                    root,
                    include_system=include_system,
                    include_parts=include_parts,
                    include_versions=include_versions,
                    interval=interval,
                )
                if register_only:
                    if baseline_complete:
                        if export_keeper is self.keeper:
                            clear_sync_outbox(self.keeper)
                        record_markdown_mirror_export_success(
                            self.keeper,
                            entry.root,
                            source_cursor=source_cursor or None,
                        )
                    self._json(200, {
                        "sync": {
                            "root": entry.root,
                            "interval": entry.interval,
                            "include_system": entry.include_system,
                            "include_parts": entry.include_parts,
                            "include_versions": entry.include_versions,
                        },
                    })
                    return
                count, info = run_markdown_export_once(
                    export_keeper,
                    entry.root,
                    include_system=entry.include_system,
                    include_parts=entry.include_parts,
                    include_versions=entry.include_versions,
                    allow_existing=True,
                    mirror_entry=entry,
                )
                if export_keeper is self.keeper:
                    clear_sync_outbox(self.keeper)
                record_markdown_mirror_export_success(self.keeper, entry.root)
                self._json(200, {
                    "sync": {
                        "root": entry.root,
                        "interval": entry.interval,
                        "include_system": entry.include_system,
                        "include_parts": entry.include_parts,
                        "include_versions": entry.include_versions,
                    },
                    "exported": {"count": count, "store_info": info},
                })
                return

            count, info = run_markdown_export_once(
                export_keeper,
                root,
                include_system=include_system,
                include_parts=include_parts,
                include_versions=include_versions,
                allow_existing=False,
                mirror_entry=None,
            )
            self._json(200, {"exported": {"count": count, "store_info": info}})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            logger.warning("Markdown export failed for %s: %s", root, exc, exc_info=True)
            self._json(500, {"error": "markdown export failed"})

    def _handle_find(self, groups: dict):
        body = self._read_body()
        results = flow_find_items(
            self.keeper,
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
        remove_values = body.get("remove_values", {})
        if remove_keys or remove_values:
            item = self.keeper.tag(
                groups["id"],
                tags=set_tags or None,
                remove=remove_keys or None,
                remove_values=remove_values or None,
            )
        else:
            item = flow_tag_item(self.keeper, groups["id"], set_tags or None)
        if item is None:
            self._json(404, {"error": "not found"})
        else:
            self._json(200, _item_to_dict(item))

    def _handle_flow(self, groups: dict):
        body = self._read_body()
        try:
            budget = int(body["budget"]) if body.get("budget") not in (None, "") else 5
        except (ValueError, TypeError):
            budget = 5
        result = self.keeper.run_flow(
            state=body.get("state", ""),
            params=body.get("params", {}),
            budget=budget,
            cursor_token=body.get("cursor_token") or body.get("cursor"),
            state_doc_yaml=body.get("state_doc_yaml"),
            writable=body.get("writable", True),
        )
        resp: dict = {
            "status": result.status,
            "bindings": result.bindings,
            "data": result.data,
            "ticks": result.ticks,
            "history": result.history,
            "cursor": result.cursor,
            "tried_queries": result.tried_queries,
        }
        try:
            token_budget = int(body["token_budget"]) if "token_budget" in body else 0
        except (ValueError, TypeError):
            token_budget = 0
        if token_budget > 0:
            from .console_support import render_flow_response
            resp["rendered"] = render_flow_response(
                result, token_budget=token_budget, keeper=self.keeper,
            )
        self._json(200, resp)

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

    def _handle_reset_system_docs(self, groups: dict):
        stats = self.keeper.reset_system_documents()
        self._json(200, stats)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

class DaemonServer:
    """HTTP server lifecycle for the daemon."""

    def __init__(
        self,
        keeper: "Keeper",
        port: int = DAEMON_PORT,
        *,
        export_keeper: Any = None,
        bind_host: str = "127.0.0.1",
        advertised_url: str | None = None,
        trusted_proxy: bool = False,
    ):
        self._keeper = keeper
        self._export_keeper = export_keeper
        self._preferred_port = port
        self._bind_host = bind_host
        self._advertised_url = advertised_url
        self._trusted_proxy = trusted_proxy
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.auth_token: str = ""

    def start(self) -> int:
        """Start the HTTP server. Returns the actual bound port."""
        self.auth_token = secrets.token_urlsafe(32)
        if not self.auth_token:
            raise RuntimeError("auth_token must be non-empty before starting daemon server")
        _validate_remote_bind_policy(
            self._bind_host,
            self._advertised_url,
            trusted_proxy=self._trusted_proxy,
        )
        if not _is_loopback_host(self._bind_host):
            logger.warning(
                "Remote daemon mode enabled on %s without in-process TLS; "
                "assuming TLS termination by a trusted proxy. Bearer credentials "
                "are not protected in transit by keep itself.",
                self._bind_host,
            )
        DaemonRequestHandler.keeper = self._keeper
        DaemonRequestHandler.export_keeper = self._export_keeper or self._keeper
        DaemonRequestHandler.auth_token = self.auth_token
        DaemonRequestHandler.bind_host = self._bind_host
        DaemonRequestHandler.advertised_url = self._advertised_url
        DaemonRequestHandler.allowed_hosts = _allowed_hosts_for_mode(
            self._bind_host,
            self._advertised_url,
        )
        try:
            self._server = ThreadingHTTPServer(
                (self._bind_host, self._preferred_port), DaemonRequestHandler)
        except OSError:
            logger.info("Port %d in use, using OS-assigned port", self._preferred_port)
            self._server = ThreadingHTTPServer(
                (self._bind_host, 0), DaemonRequestHandler)

        port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="daemon-http")
        self._thread.start()
        logger.info("Query server listening on %s:%d", self._bind_host, port)
        return port

    def stop(self):
        """Shut down the HTTP server and release the listening socket."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Query server stopped")
