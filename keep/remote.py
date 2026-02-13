"""
Remote Keeper — HTTP client for the keepnotes.ai API.

Implements KeeperProtocol by mapping method calls to REST endpoints.
Used when a [remote] section is configured in keep.toml or when
KEEPNOTES_API_URL and KEEPNOTES_API_KEY environment variables are set.
"""

import logging
from typing import Any, Optional

import httpx

from .config import StoreConfig
from .document_store import VersionInfo
from .types import Item

logger = logging.getLogger(__name__)

# Default timeout for API calls (seconds)
DEFAULT_TIMEOUT = 30.0


class RemoteKeeper:
    """
    Keeper backend that delegates to a remote keepnotes.ai API.

    Satisfies KeeperProtocol — the CLI uses it interchangeably with
    the local Keeper class.
    """

    def __init__(self, api_url: str, api_key: str, config: StoreConfig):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._config = config

        # Refuse non-HTTPS for remote APIs (bearer token would be sent in cleartext)
        if not self.api_url.startswith("https://") and "localhost" not in self.api_url and "127.0.0.1" not in self.api_url:
            raise ValueError(
                f"Remote API URL must use HTTPS (got {self.api_url}). "
                "Use HTTPS to protect API credentials, or use localhost for local development."
            )

        self._client = httpx.Client(
            base_url=self.api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT,
        )

    # -- HTTP helpers --

    def _get(self, path: str, **params: Any) -> dict:
        """GET request, return parsed JSON."""
        # Filter out None params
        filtered = {k: v for k, v in params.items() if v is not None}
        resp = self._client.get(path, params=filtered)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json: dict) -> dict:
        """POST request with JSON body."""
        # Filter out None values
        filtered = {k: v for k, v in json.items() if v is not None}
        resp = self._client.post(path, json=filtered)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, json: dict) -> dict:
        """PUT request with JSON body."""
        filtered = {k: v for k, v in json.items() if v is not None}
        resp = self._client.put(path, json=filtered)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, json: dict) -> dict:
        """PATCH request with JSON body."""
        resp = self._client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> dict:
        """DELETE request."""
        resp = self._client.delete(path)
        resp.raise_for_status()
        return resp.json()

    # -- Response conversion --

    @staticmethod
    def _to_item(data: dict) -> Item:
        """Convert API response dict to Item, with basic validation."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict from API, got {type(data).__name__}")
        item_id = data.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"API response missing valid 'id' field: {data!r:.200}")
        tags = data.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        # Ensure tag keys and values are strings
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
        return Item(
            id=item_id,
            summary=summary,
            tags=tags,
            score=score,
        )

    @staticmethod
    def _to_items(data: dict) -> list[Item]:
        """Convert API list response to list of Items."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict from API, got {type(data).__name__}")
        items = data.get("notes", data.get("items", []))
        if not isinstance(items, list):
            raise ValueError(f"Expected 'notes' list from API, got {type(items).__name__}")
        return [RemoteKeeper._to_item(item) for item in items]

    @staticmethod
    def _to_version_info(data: dict) -> VersionInfo:
        """Convert API response dict to VersionInfo."""
        return VersionInfo(
            version=data["version"],
            summary=data.get("summary", ""),
            tags=data.get("tags", {}),
            created_at=data.get("created_at", ""),
            content_hash=data.get("content_hash"),
        )

    # -- Write operations --

    def update(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
        *,
        summary: Optional[str] = None,
    ) -> Item:
        resp = self._post("/v1/notes", json={
            "uri": id,
            "tags": tags,
            "summary": summary,
        })
        return self._to_item(resp)

    def remember(
        self,
        content: str,
        *,
        id: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> Item:
        resp = self._post("/v1/notes", json={
            "content": content,
            "id": id,
            "tags": tags,
            "summary": summary,
        })
        return self._to_item(resp)

    def set_now(
        self,
        content: str,
        *,
        tags: Optional[dict[str, str]] = None,
    ) -> Item:
        resp = self._put("/v1/now", json={
            "content": content,
            "tags": tags,
        })
        return self._to_item(resp)

    def tag(
        self,
        id: str,
        tags: Optional[dict[str, str]] = None,
    ) -> Optional[Item]:
        if tags is None:
            return self.get(id)
        resp = self._patch(f"/v1/notes/{id}/tags", json={
            "set": {k: v for k, v in tags.items() if v},
            "remove": [k for k, v in tags.items() if not v],
        })
        return self._to_item(resp)

    def delete(
        self,
        id: str,
        *,
        delete_versions: bool = True,
    ) -> bool:
        resp = self._delete(f"/v1/notes/{id}")
        return resp.get("deleted", False)

    def revert(self, id: str) -> Optional[Item]:
        resp = self._post(f"/v1/notes/{id}/revert", json={})
        if resp.get("deleted"):
            return None
        return self._to_item(resp)

    def move(
        self,
        name: str,
        *,
        source_id: str = "now",
        tags: Optional[dict[str, str]] = None,
        only_current: bool = False,
    ) -> Item:
        resp = self._post("/v1/move", json={
            "target": name,
            "source": source_id,
            "tags": tags,
            "only_current": only_current,
        })
        return self._to_item(resp)

    # -- Query operations --

    def find(
        self,
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
        include_hidden: bool = False,
    ) -> list[Item]:
        resp = self._post("/v1/search", json={
            "query": query,
            "limit": limit,
            "since": since,
            "include_hidden": include_hidden or None,
        })
        return self._to_items(resp)

    def find_similar(
        self,
        id: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
        include_self: bool = False,
        include_hidden: bool = False,
    ) -> list[Item]:
        resp = self._post(f"/v1/search/similar/{id}", json={
            "limit": limit,
            "since": since,
            "include_self": include_self,
            "include_hidden": include_hidden or None,
        })
        return self._to_items(resp)

    def get_similar_for_display(
        self,
        id: str,
        *,
        limit: int = 3,
    ) -> list[Item]:
        resp = self._get(f"/v1/notes/{id}/similar", limit=limit)
        return self._to_items(resp)

    def query_fulltext(
        self,
        query: str,
        *,
        limit: int = 10,
        since: Optional[str] = None,
        include_hidden: bool = False,
    ) -> list[Item]:
        resp = self._post("/v1/search/fulltext", json={
            "query": query,
            "limit": limit,
            "since": since,
            "include_hidden": include_hidden or None,
        })
        return self._to_items(resp)

    def query_tag(
        self,
        key: Optional[str] = None,
        value: Optional[str] = None,
        *,
        limit: int = 100,
        since: Optional[str] = None,
        include_hidden: bool = False,
    ) -> list[Item]:
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        if include_hidden:
            params["include_hidden"] = True
        if key and value:
            resp = self._get(f"/v1/tags/{key}/{value}", **params)
        elif key:
            resp = self._get(f"/v1/tags/{key}", **params)
        else:
            resp = self._get("/v1/notes", **params)
        return self._to_items(resp)

    def list_tags(
        self,
        key: Optional[str] = None,
    ) -> list[str]:
        if key:
            resp = self._get(f"/v1/tags/{key}")
        else:
            resp = self._get("/v1/tags")
        return resp.get("values", [])

    def resolve_meta(
        self,
        item_id: str,
        *,
        limit_per_doc: int = 3,
    ) -> dict[str, list[Item]]:
        resp = self._get(f"/v1/notes/{item_id}/meta", limit=limit_per_doc)
        result: dict[str, list[Item]] = {}
        for name, items_data in resp.get("sections", {}).items():
            result[name] = [self._to_item(i) for i in items_data]
        return result

    def resolve_inline_meta(
        self,
        item_id: str,
        queries: list[dict[str, str]],
        context_keys: list[str] | None = None,
        prereq_keys: list[str] | None = None,
        *,
        limit: int = 3,
    ) -> list[Item]:
        resp = self._post(f"/v1/notes/{item_id}/resolve", json={
            "queries": queries,
            "context_keys": context_keys,
            "prerequisites": prereq_keys,
            "limit": limit,
        })
        return self._to_items(resp)

    def list_recent(
        self,
        limit: int = 10,
        *,
        since: Optional[str] = None,
        order_by: str = "updated",
        include_history: bool = False,
        include_hidden: bool = False,
    ) -> list[Item]:
        resp = self._get(
            "/v1/notes",
            limit=limit,
            since=since,
            order_by=order_by,
            include_history=include_history,
            include_hidden=include_hidden or None,
        )
        return self._to_items(resp)

    # -- Direct access --

    def get(self, id: str) -> Optional[Item]:
        try:
            resp = self._get(f"/v1/notes/{id}")
            return self._to_item(resp)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_now(self) -> Item:
        resp = self._get("/v1/now")
        return self._to_item(resp)

    def get_version(
        self,
        id: str,
        offset: int = 0,
    ) -> Optional[Item]:
        try:
            resp = self._get(f"/v1/notes/{id}/versions/{offset}")
            return self._to_item(resp)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def list_versions(
        self,
        id: str,
        limit: int = 10,
    ) -> list[VersionInfo]:
        resp = self._get(f"/v1/notes/{id}/versions", limit=limit)
        return [self._to_version_info(v) for v in resp.get("versions", [])]

    def get_version_nav(
        self,
        id: str,
        current_version: Optional[int] = None,
        limit: int = 3,
    ) -> dict:
        resp = self._get(
            f"/v1/notes/{id}/versions/nav",
            current_version=current_version,
            limit=limit,
        )
        result: dict[str, list[VersionInfo]] = {}
        for key in ("prev", "next"):
            if key in resp:
                result[key] = [self._to_version_info(v) for v in resp[key]]
        return result

    def get_version_offset(self, item: Item) -> int:
        resp = self._get(f"/v1/notes/{item.id}/version-offset")
        return resp.get("offset", 0)

    def exists(self, id: str) -> bool:
        try:
            self._get(f"/v1/notes/{id}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise

    # -- Collection management --

    def list_collections(self) -> list[str]:
        resp = self._get("/v1/collections")
        return resp.get("collections", [])

    def count(self) -> int:
        resp = self._get("/v1/count")
        return resp.get("count", 0)

    def close(self) -> None:
        self._client.close()
