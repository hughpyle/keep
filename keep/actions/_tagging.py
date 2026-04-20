from __future__ import annotations

"""Spec-driven tagging helpers shared by state actions."""

from typing import Any

from ..analyzers import TagClassifier, extract_prompt_section


def _truthy(value: Any) -> bool:
    """Return True for conventional truthy textual values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _item_id(item: Any) -> str:
    """Return a normalized item ID string."""
    return str(getattr(item, "id", "")).strip()


def _item_summary(item: Any) -> str:
    """Return an item's summary text."""
    return str(getattr(item, "summary", "") or "")


def _item_tags(item: Any) -> dict[str, Any]:
    """Return an item's tag mapping."""
    raw = getattr(item, "tags", None)
    return dict(raw) if isinstance(raw, dict) else {}


def load_tag_specs(context: Any, *, limit: int = 5000) -> list[dict[str, Any]]:
    """Load constrained `.tag/*` specs through action context adapters."""
    fetch_limit = max(int(limit), 1)
    try:
        docs = context.list_items(
            prefix=".tag/",
            include_hidden=True,
            limit=fetch_limit,
        )
    except Exception:
        return []
    if not isinstance(docs, list) or not docs:
        return []

    parents: dict[str, Any] = {}
    children: dict[str, list[Any]] = {}
    for item in docs:
        doc_id = _item_id(item)
        if not doc_id.startswith(".tag/"):
            continue
        parts = doc_id.split("/")
        if len(parts) == 2:
            key = parts[1].strip()
            if key:
                parents[key] = item
            continue
        if len(parts) == 3:
            key = parts[1].strip()
            if key:
                children.setdefault(key, []).append(item)

    specs: list[dict[str, Any]] = []
    for key in sorted(parents.keys()):
        parent = parents[key]
        parent_tags = _item_tags(parent)
        if not _truthy(parent_tags.get("_constrained")):
            continue
        parent_summary = _item_summary(parent)
        values: list[dict[str, Any]] = []
        for child in sorted(children.get(key, []), key=_item_id):
            child_id = _item_id(child)
            value = child_id.split("/")[-1].strip()
            if not value:
                continue
            child_summary = _item_summary(child)
            values.append(
                {
                    "value": value,
                    "description": child_summary,
                    "prompt": extract_prompt_section(child_summary),
                }
            )
        spec: dict[str, Any] = {
            "key": key,
            "description": parent_summary,
            "prompt": extract_prompt_section(parent_summary),
            "values": values,
        }
        # Carry _when for downstream filtering by item context
        when_source = parent_tags.get("_when", "")
        if when_source:
            spec["_when"] = when_source
        specs.append(spec)

    return specs


_cel_compile_cache: dict[str, Any] = {}  # compiled CEL programs keyed by source


def _filter_specs_by_when(
    specs: list[dict[str, Any]],
    item_tags: dict[str, Any],
    item_id: str = "",
    item_summary: str = "",
) -> list[dict[str, Any]]:
    """Remove specs whose ``_when`` condition is not met by the item."""
    from ..state_doc import _compile_predicate, _eval_predicate
    from ..types import build_item_context
    import logging

    logger = logging.getLogger(__name__)
    result = []
    for spec in specs:
        when_source = spec.get("_when", "")
        if not when_source:
            result.append(spec)
            continue
        try:
            prog = _cel_compile_cache.get(when_source)
            if prog is None:
                prog = _compile_predicate(when_source)
                _cel_compile_cache[when_source] = prog
            ctx = build_item_context(
                id=item_id,
                tags=item_tags,
                summary=item_summary,
                content_type=item_tags.get("_content_type", ""),
                uri=item_tags.get("_source_uri", ""),
            )
            if _eval_predicate(prog, {"item": ctx}, when_source):
                result.append(spec)
        except (ValueError, RuntimeError) as exc:
            logger.warning(
                ".tag/%s: failed to evaluate _when %r: %s",
                spec.get("key", "?"), when_source, exc,
            )
    return result


def classify_parts_with_specs(
    parts: list[dict[str, Any]],
    context: Any,
    *,
    item_tags: dict[str, Any] | None = None,
    item_id: str = "",
    item_summary: str = "",
) -> list[dict[str, Any]]:
    """Classify part summaries with constrained tag specs when available.

    When *item_tags* is provided, specs with ``_when`` conditions are
    filtered: only specs whose condition matches the item's tags are
    included in the classification prompt.
    """
    if not parts:
        return parts
    specs = load_tag_specs(context)
    if not specs:
        return parts
    # Filter specs by _when conditions if item context is available
    if item_tags is not None:
        specs = _filter_specs_by_when(specs, item_tags, item_id, item_summary)
        if not specs:
            return parts
    provider = context.resolve_provider("summarization")
    # Resolve prompt template from .prompt/tag/* docs
    prompt_template = None
    try:
        prompt_template = context.resolve_prompt("tag") if hasattr(context, "resolve_prompt") else None
    except Exception:
        pass
    classifier = TagClassifier(provider=provider)
    return classifier.classify(parts, specs=specs, prompt_template=prompt_template)
