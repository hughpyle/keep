"""Shared markdown frontmatter key policy.

The sync/export contract is defined in ``later/design/markdown-sync-design.md``.
This module keeps the key classification in one place so markdown export,
generic frontmatter parsing, and future sync import all make the same decision
about identity metadata, reserved keys, and writable tags.
"""

from __future__ import annotations

from typing import Literal


MARKDOWN_FRONTMATTER_ID_KEY = "_id"
MARKDOWN_FRONTMATTER_TAGS_KEY = "tags"

# Exporter-owned reserved frontmatter keys. These are emitted by markdown
# export and should be treated as read-only metadata by future sync import.
MARKDOWN_EXPORTER_OWNED_KEYS = frozenset({
    MARKDOWN_FRONTMATTER_ID_KEY,
    "_content_hash",
    "_content_hash_full",
    "_part_num",
    "_version",
    "_version_offset",
    "_prev_part",
    "_next_part",
    "_prev_version",
    "_next_version",
})

MarkdownFrontmatterKeyKind = Literal[
    "identity",
    "reserved",
    "tag_namespace",
    "writable_tag",
]


def classify_markdown_frontmatter_key(key: str) -> MarkdownFrontmatterKeyKind:
    """Classify a top-level markdown frontmatter key for export/import policy."""
    if key == MARKDOWN_FRONTMATTER_ID_KEY:
        return "identity"
    if key == MARKDOWN_FRONTMATTER_TAGS_KEY:
        return "tag_namespace"
    if key.startswith("_"):
        return "reserved"
    return "writable_tag"


def is_writable_markdown_frontmatter_key(key: str) -> bool:
    """Return True for top-level frontmatter keys that should become note tags."""
    return classify_markdown_frontmatter_key(key) == "writable_tag"
