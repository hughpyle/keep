from __future__ import annotations

from typing import Any

from . import action


@action(id="stub")
class Stub:
    """Create a stub note if absent, preserving existing notes."""

    def run(self, params: dict[str, Any], context) -> dict[str, Any]:
        item_id = params.get("id")
        if item_id is None or str(item_id).strip() == "":
            raise ValueError("stub requires id")

        tags = params.get("tags")
        normalized_tags = {str(k): v for k, v in tags.items()} if isinstance(tags, dict) else None
        summary = params.get("summary")
        created_at = params.get("created_at")
        raw_queue_background_tasks = params.get("queue_background_tasks")
        queue_background_tasks = (
            True if raw_queue_background_tasks is None else bool(raw_queue_background_tasks)
        )
        content = params.get("content")

        item = context.stub(
            id=str(item_id),
            content=str(content) if content is not None else None,
            tags=normalized_tags,
            summary=str(summary) if summary is not None else None,
            created_at=str(created_at) if created_at is not None else None,
            queue_background_tasks=queue_background_tasks,
        )
        return {
            "id": getattr(item, "id", None),
            "summary": getattr(item, "summary", None),
            "tags": dict(getattr(item, "tags", None) or {}),
            "changed": getattr(item, "changed", None),
        }
