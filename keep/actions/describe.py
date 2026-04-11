from __future__ import annotations

"""Media description action for URI-backed non-text content."""

import logging
from pathlib import Path
from typing import Any

from ..paths import validate_path_within_home
from ..types import file_uri_to_path
from . import action
from ._item_scope import resolve_item

logger = logging.getLogger(__name__)


def _resolve_local_media_path(uri: str) -> Path | None:
    """Return a local media path for ``describe``, or ``None`` if unsupported."""
    if not uri:
        return None
    if uri.startswith("file://"):
        return Path(file_uri_to_path(uri)).resolve()
    if uri.startswith("/"):
        return Path(uri).resolve()
    return None


@action(id="describe", priority=5, async_action=True)
class Describe:
    """Generate a text description of media content (images, audio, video)."""

    def run(self, params: dict[str, Any], context) -> dict[str, Any]:
        """Describe media content and emit a summary mutation."""
        item_id, item = resolve_item(params, context)

        tags = getattr(item, "tags", None)
        tags = dict(tags) if isinstance(tags, dict) else {}
        uri = str(
            params.get("uri")
            or getattr(item, "uri", "")
            or tags.get("_source_uri")
            or item_id
        ).strip()
        content_type = str(
            params.get("content_type") or tags.get("_content_type") or ""
        ).strip()

        describer = context.resolve_provider("media")
        describe_fn = getattr(describer, "describe", None)
        if not callable(describe_fn):
            return {"description": "", "skipped": True}

        path = _resolve_local_media_path(uri)
        if path is None:
            logger.info("Describe requires a local file URI/path, skipping: %s", uri)
            return {"description": "", "skipped": True, "reason": "non_local_uri"}

        if not path.exists():
            logger.warning("File no longer exists for describe: %s", path)
            return {"description": "", "skipped": True, "reason": "missing_file"}

        try:
            validate_path_within_home(path)
        except ValueError:
            logger.warning("Describe path outside home directory, skipping: %s", path)
            return {"description": "", "skipped": True, "reason": "path_outside_home"}

        try:
            description = describe_fn(str(path), content_type)
        except Exception as e:
            logger.warning("Media description failed for %s: %s", uri, e)
            return {"description": "", "skipped": True, "reason": f"describe_error: {e}"}

        if not description or not description.strip():
            return {"description": "", "skipped": True, "reason": "empty_description"}

        # Append to existing summary
        existing_summary = str(getattr(item, "summary", "") or "")
        if existing_summary:
            enriched = existing_summary.rstrip() + "\n\nDescription:\n" + description
        else:
            enriched = description

        return {
            "description": description,
            "mutations": [
                {
                    "op": "set_summary",
                    "target": item_id,
                    "summary": enriched,
                    "embed": True,
                    "intent": "derived_description_append",
                }
            ],
        }
