"""Remote Keeper — HTTP client for keep's 7-endpoint API.

Used by the CLI to talk to the local daemon, and by the hosted
keepnotes.ai service.  Implements KeeperProtocol through 7 endpoints:
health, get, put, delete, find, tag, flow.

Everything beyond these core operations goes through the flow endpoint.
"""

import logging
import os
import re
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .config import StoreConfig
from .types import (
    Item, ItemContext, SimilarRef, MetaRef, EdgeRef, VersionRef, PartRef,
    TagMap, local_date,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

_SLUG_RE = re.compile(r'^[a-z][a-z0-9-]{0,61}[a-z0-9]$')


class RemoteKeeper:
    """Keeper backend speaking the 7-endpoint API.

    Core endpoints (direct HTTP):
        GET    /v1/notes/{id}          get
        POST   /v1/notes               put
        DELETE /v1/notes/{id}          delete
        POST   /v1/search              find
        PATCH  /v1/notes/{id}/tags     tag
        POST   /v1/flow                run_flow_command

    Everything else (context, versions, parts, meta, prompts, etc.)
    goes through run_flow_command().
    """

    def __init__(self, api_url: str, api_key: str, config: StoreConfig, *, project: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._config = config
        self.config = config  # alias for CLI compatibility

        self.project = (
            project
            or (config.remote.project if config.remote else None)
            or os.environ.get("KEEPNOTES_PROJECT")
            or None
        )
        if self.project and not _SLUG_RE.match(self.project):
            raise ValueError(
                f"Invalid project slug '{self.project}'. "
                "Must start with a letter, 2-63 chars, lowercase letters/numbers/hyphens."
            )

        if not self.api_url.startswith("https://"):
            from urllib.parse import urlparse
            host = urlparse(self.api_url).hostname or ""
            if host not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError(
                    f"Remote API URL must use HTTPS (got {self.api_url}). "
                    "Use HTTPS to protect API credentials, or use localhost for local development."
                )

        from .types import user_agent
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": user_agent(),
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if self.project:
            headers["X-Project"] = self.project

        self._client = httpx.Client(
            base_url=self.api_url, headers=headers, timeout=DEFAULT_TIMEOUT)

    # -- HTTP helpers --

    @staticmethod
    def _q(id: str) -> str:
        return quote(id, safe="")

    def _get(self, path: str, **params: Any) -> dict:
        filtered = {k: v for k, v in params.items() if v is not None}
        resp = self._client.get(path, params=filtered)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json: dict) -> dict:
        filtered = {k: v for k, v in json.items() if v is not None}
        resp = self._client.post(path, json=filtered)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, json: dict) -> dict:
        resp = self._client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> dict:
        resp = self._client.delete(path)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _to_item(data: dict) -> Item:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        item_id = data.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"Missing 'id' in response: {data!r:.200}")
        tags = data.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        tags = {str(k): str(v) for k, v in tags.items()}
        if data.get("created_at"):
            tags.setdefault("_created", str(data["created_at"]))
        if data.get("updated_at"):
            tags.setdefault("_updated", str(data["updated_at"]))
        summary = data.get("summary", "")
        if not isinstance(summary, str):
            summary = str(summary)
        score = data.get("score")
        if score is not None:
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = None
        return Item(id=item_id, summary=summary, tags=tags, score=score)

    @staticmethod
    def _to_items(data: dict) -> list[Item]:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        items = data.get("notes", data.get("items", []))
        if not isinstance(items, list):
            raise ValueError(f"Expected list, got {type(items).__name__}")
        return [RemoteKeeper._to_item(i) for i in items]

    # ---- Core: 6 direct endpoints ----

    def get(self, id: str) -> Optional[Item]:
        try:
            return self._to_item(self._get(f"/v1/notes/{self._q(id)}"))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def put(
        self,
        content: Optional[str] = None,
        *,
        uri: Optional[str] = None,
        id: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[TagMap] = None,
        created_at: Optional[str] = None,
        force: bool = False,
    ) -> Item:
        return self._to_item(self._post("/v1/notes", json={
            "content": content, "uri": uri, "id": id,
            "tags": tags, "summary": summary, "created_at": created_at,
            "force": force or None,
        }))

    def delete(self, id: str, *, delete_versions: bool = True) -> bool:
        return self._delete(f"/v1/notes/{self._q(id)}").get("deleted", False)

    def find(
        self,
        query: Optional[str] = None,
        *,
        tags: Optional[TagMap] = None,
        similar_to: Optional[str] = None,
        limit: int = 10,
        since: Optional[str] = None,
        until: Optional[str] = None,
        include_self: bool = False,
        include_hidden: bool = False,
        deep: bool = False,
        scope: Optional[str] = None,
    ) -> list[Item]:
        from .api import FindResults
        resp = self._post("/v1/search", json={
            "query": query, "similar_to": similar_to, "tags": tags,
            "limit": limit, "since": since, "until": until,
            "include_self": include_self or None,
            "include_hidden": include_hidden or None,
            "deep": deep or None, "scope": scope,
        })
        items = self._to_items(resp)
        deep_groups: dict[str, list[Item]] = {}
        for g in resp.get("deep_groups", []):
            pid = g.get("id", "")
            if pid and "items" in g:
                deep_groups[pid] = [self._to_item(i) for i in g["items"]]
        return FindResults(items, deep_groups=deep_groups)

    def tag(self, id: str, tags: Optional[TagMap] = None) -> Optional[Item]:
        if tags is None:
            return self.get(id)
        return self._to_item(self._patch(f"/v1/notes/{self._q(id)}/tags", json={
            "set": {k: v for k, v in tags.items() if v},
            "remove": [k for k, v in tags.items() if not v],
        }))

    def run_flow_command(
        self,
        state: str,
        *,
        params: Optional[dict[str, Any]] = None,
        budget: Optional[int] = None,
        cursor_token: Optional[str] = None,
        state_doc_yaml: Optional[str] = None,
        writable: bool = True,
    ) -> Any:
        from .state_doc_runtime import FlowResult
        resp = self._post("/v1/flow", json={
            "state": state, "params": params, "budget": budget,
            "cursor_token": cursor_token, "state_doc_yaml": state_doc_yaml,
            "writable": writable,
        })
        return FlowResult(
            status=resp.get("status", "error"),
            bindings=resp.get("bindings", {}),
            data=resp.get("data"),
            ticks=resp.get("ticks", 0),
            history=resp.get("history", []),
            cursor=resp.get("cursor"),
        )

    # ---- Flow-based convenience methods ----

    def get_context(
        self,
        id: str,
        *,
        version: int | None = None,
        similar_limit: int = 3,
        meta_limit: int = 3,
        parts_limit: int = 10,
        edges_limit: int = 5,
        versions_limit: int = 3,
        include_similar: bool = True,
        include_meta: bool = True,
        include_parts: bool = True,
        include_versions: bool = True,
    ) -> ItemContext | None:
        """Assemble display context via the /context endpoint."""
        params: dict[str, str | int] = {
            "similar_limit": similar_limit if include_similar else 0,
            "meta_limit": meta_limit if include_meta else 0,
            "parts_limit": parts_limit if include_parts else 0,
            "edges_limit": edges_limit,
            "versions_limit": versions_limit if include_versions else 0,
            "include_similar": str(include_similar).lower(),
            "include_meta": str(include_meta).lower(),
            "include_parts": str(include_parts).lower(),
            "include_versions": str(include_versions).lower(),
        }
        if version is not None:
            params["version"] = version
        resp = self._client.get(f"/v1/notes/{self._q(id)}/context", params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return ItemContext.from_dict(resp.json())

    def get_now(self, *, scope: Optional[str] = None) -> Item:
        doc_id = f"now:{scope}" if scope else "now"
        return self.get(doc_id)

    def set_now(self, content: str, *, scope: Optional[str] = None, tags: Optional[TagMap] = None) -> Item:
        doc_id = f"now:{scope}" if scope else "now"
        merged_tags = dict(tags or {})
        if scope:
            merged_tags.setdefault("user", scope)
        return self.put(content, id=doc_id, tags=merged_tags or None)

    def exists(self, id: str) -> bool:
        return self.get(id) is not None

    def count(self) -> int:
        try:
            resp = self._client.get("/v1/health")
            if resp.status_code == 200:
                return resp.json().get("item_count", 0)
        except Exception:
            pass
        return 0

    def close(self) -> None:
        self._client.close()
