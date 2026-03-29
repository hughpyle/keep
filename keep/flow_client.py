"""Shared flow-backed client wrappers.

These helpers make the public memory API a thin layer over ``run_flow``.
The underlying flow names are intentionally internal for now; Unit 2
will converge them onto canonical state-doc names and transport shims.
"""

from __future__ import annotations

from typing import Any, Optional

from .protocol import FlowHostProtocol
from .types import Item, TagMap


FLOW_STATE_GET_ITEM = "item-get"
FLOW_STATE_PUT_ITEM = "item-put"
FLOW_STATE_FIND_ITEMS = "item-find"
FLOW_STATE_TAG_ITEM = "item-tag"
FLOW_STATE_DELETE_ITEM = "item-delete"
FLOW_STATE_GET_NOW = "item-get-now"


def _expect_done(result: Any, state: str) -> Any:
    status = getattr(result, "status", None)
    if status != "done":
        data = getattr(result, "data", None)
        raise ValueError(f"flow {state!r} failed with status {status!r}: {data!r}")
    return result


def _coerce_item(data: Any) -> Item:
    if not isinstance(data, dict):
        raise ValueError(f"Expected item dict, got {type(data).__name__}")
    item_id = data.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError(f"Missing item id in {data!r}")
    tags = data.get("tags", {})
    if not isinstance(tags, dict):
        tags = {}
    score = data.get("score")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
    changed = data.get("changed")
    if changed is not None:
        changed = bool(changed)
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    return Item(
        id=item_id,
        summary=summary,
        tags={str(k): v for k, v in tags.items()},
        score=score,
        changed=changed,
    )


def _coerce_item_list(items: Any) -> list[Item]:
    if not isinstance(items, list):
        raise ValueError(f"Expected list of items, got {type(items).__name__}")
    return [_coerce_item(item) for item in items]


def get_item(host: FlowHostProtocol, id: str) -> Optional[Item]:
    result = _expect_done(
        host.run_flow(FLOW_STATE_GET_ITEM, params={"id": id}, writable=False),
        FLOW_STATE_GET_ITEM,
    )
    data = getattr(result, "data", None) or {}
    item = data.get("item")
    if item is None:
        return None
    return _coerce_item(item)


def put_item(
    host: FlowHostProtocol,
    content: Optional[str] = None,
    *,
    uri: Optional[str] = None,
    id: Optional[str] = None,
    summary: Optional[str] = None,
    tags: Optional[TagMap] = None,
    created_at: Optional[str] = None,
    force: bool = False,
) -> Item:
    result = _expect_done(
        host.run_flow(
            FLOW_STATE_PUT_ITEM,
            params={
                "content": content,
                "uri": uri,
                "id": id,
                "summary": summary,
                "tags": tags,
                "created_at": created_at,
                "force": force,
            },
        ),
        FLOW_STATE_PUT_ITEM,
    )
    data = getattr(result, "data", None) or {}
    return _coerce_item(data["item"])


def find_items(
    host: FlowHostProtocol,
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
    result = _expect_done(
        host.run_flow(
            FLOW_STATE_FIND_ITEMS,
            params={
                "query": query,
                "tags": tags,
                "similar_to": similar_to,
                "limit": limit,
                "since": since,
                "until": until,
                "include_self": include_self,
                "include_hidden": include_hidden,
                "deep": deep,
                "scope": scope,
            },
            writable=False,
        ),
        FLOW_STATE_FIND_ITEMS,
    )
    data = getattr(result, "data", None) or {}
    items = _coerce_item_list(data.get("items", []))
    deep_groups_raw = data.get("deep_groups", {})
    deep_groups: dict[str, list[Item]] = {}
    if isinstance(deep_groups_raw, dict):
        for key, values in deep_groups_raw.items():
            deep_groups[str(key)] = _coerce_item_list(values)
    try:
        from .api import FindResults
        return FindResults(items, deep_groups=deep_groups)
    except Exception:
        return items


def tag_item(
    host: FlowHostProtocol,
    id: str,
    tags: Optional[TagMap] = None,
) -> Optional[Item]:
    if tags is None:
        return get_item(host, id)
    result = _expect_done(
        host.run_flow(FLOW_STATE_TAG_ITEM, params={"id": id, "tags": tags}),
        FLOW_STATE_TAG_ITEM,
    )
    data = getattr(result, "data", None) or {}
    item = data.get("item")
    if item is None:
        return None
    return _coerce_item(item)


def delete_item(
    host: FlowHostProtocol,
    id: str,
    *,
    delete_versions: bool = True,
) -> bool:
    result = _expect_done(
        host.run_flow(
            FLOW_STATE_DELETE_ITEM,
            params={"id": id, "delete_versions": delete_versions},
        ),
        FLOW_STATE_DELETE_ITEM,
    )
    data = getattr(result, "data", None) or {}
    return bool(data.get("deleted", False))


def get_now_item(host: FlowHostProtocol, *, scope: Optional[str] = None) -> Item:
    result = _expect_done(
        host.run_flow(FLOW_STATE_GET_NOW, params={"scope": scope}, writable=False),
        FLOW_STATE_GET_NOW,
    )
    data = getattr(result, "data", None) or {}
    return _coerce_item(data["item"])


def set_now_item(
    host: FlowHostProtocol,
    content: str,
    *,
    scope: Optional[str] = None,
    tags: Optional[TagMap] = None,
) -> Item:
    doc_id = f"now:{scope}" if scope else "now"
    merged_tags = dict(tags or {})
    if scope:
        merged_tags.setdefault("user", scope)
    return put_item(host, content, id=doc_id, tags=merged_tags or None)
