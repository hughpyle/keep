"""
Data types for reflective memory.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# System tag prefix - tags starting with this are managed by the system
SYSTEM_TAG_PREFIX = "_"

# Tags used internally but hidden from display output
# These exist for efficient queries/sorting but aren't user-facing
INTERNAL_TAGS = frozenset({"_updated_date", "_accessed_date"})


def utc_now() -> str:
    """Current UTC timestamp in canonical format: YYYY-MM-DDTHH:MM:SS.

    All timestamps in keep are UTC, stored without timezone suffix.
    This is the single source of truth for timestamp formatting.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_utc_timestamp(ts: str) -> datetime:
    """Parse a stored timestamp string to a timezone-aware UTC datetime.

    Handles both the canonical format (no suffix) and legacy formats
    that may include microseconds, 'Z', or '+00:00' suffixes.
    """
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_date(utc_iso: str) -> str:
    """Convert a UTC ISO timestamp to a local-timezone date string (YYYY-MM-DD).

    Used for short-form display dates. Returns empty string for empty/invalid input.
    """
    if not utc_iso:
        return ""
    try:
        dt = parse_utc_timestamp(utc_iso)
        return dt.astimezone().strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return utc_iso[:10] if len(utc_iso) >= 10 else utc_iso


# Tag keys must be simple: alphanumeric, underscore, hyphen (no JSON path chars)
_TAG_KEY_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')

MAX_ID_LENGTH = 1024
MAX_TAG_KEY_LENGTH = 128
MAX_TAG_VALUE_LENGTH = 4096


def validate_tag_key(key: str) -> None:
    """Validate a tag key is safe for JSON path queries."""
    if not key or len(key) > MAX_TAG_KEY_LENGTH:
        raise ValueError(f"Tag key must be 1-{MAX_TAG_KEY_LENGTH} characters: {key!r}")
    if not _TAG_KEY_RE.match(key):
        raise ValueError(f"Tag key contains invalid characters (allowed: a-z, 0-9, _, -): {key!r}")


def validate_id(id: str) -> None:
    """Validate a document ID length."""
    if not id or len(id) > MAX_ID_LENGTH:
        raise ValueError(f"ID must be 1-{MAX_ID_LENGTH} characters")


def filter_non_system_tags(tags: dict[str, str]) -> dict[str, str]:
    """
    Filter out any system tags (those starting with '_').
    
    Use this to ensure source tags and derived tags cannot
    overwrite system-managed values.
    """
    return {k: v for k, v in tags.items() if not k.startswith(SYSTEM_TAG_PREFIX)}


@dataclass(frozen=True)
class Item:
    """
    An item retrieved from the reflective memory store.
    
    This is a read-only snapshot. To modify an item, use api.update()
    which returns a new Item with updated values.
    
    Timestamps and other system metadata live in tags, not as explicit fields.
    This follows the "schema as data" principle.
    
    Attributes:
        id: URI or custom identifier for the item
        summary: Generated summary of the content
        tags: All tags (source, system, and generated combined)
        score: Similarity score (present only in search results)
    
    System tags (managed automatically, in tags dict):
        _created: ISO timestamp when first indexed
        _updated: ISO timestamp when last indexed
        _updated_date: Date portion for easier queries
        _accessed: ISO timestamp when last retrieved
        _accessed_date: Date portion for easier queries
        _content_type: MIME type if known
        _source: How content was obtained (uri, inline)
        _session: Session that last touched this item
    """
    id: str
    summary: str
    tags: dict[str, str] = field(default_factory=dict)
    score: Optional[float] = None
    
    @property
    def created(self) -> str | None:
        """ISO timestamp when first indexed (from _created tag)."""
        return self.tags.get("_created")
    
    @property
    def updated(self) -> str | None:
        """ISO timestamp when last indexed (from _updated tag)."""
        return self.tags.get("_updated")

    @property
    def accessed(self) -> str | None:
        """ISO timestamp when last retrieved (from _accessed tag)."""
        return self.tags.get("_accessed")
    
    def __str__(self) -> str:
        score_str = f" [{self.score:.3f}]" if self.score is not None else ""
        return f"{self.id}{score_str}: {self.summary[:60]}..."
