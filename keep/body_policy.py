"""Shared body-authority and write-intent policy helpers.

Full policy rationale and the per-write-site inventory live in
``later/design/markdown-sync-content-role-policy.md``. Every note may carry a
``_body_authority`` system tag whose value decides whether derived-text
mutations may overwrite the stored body. The default is ``derived``; existing
stores need no migration because an absent tag means derived behavior.
``markdown`` marks the note body as authoritative authored markdown content
and rejects non-authoritative body writes.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping


BODY_AUTHORITY_TAG = "_body_authority"
# Default for any note that does not explicitly opt in to authored-markdown
# semantics. Existing stores therefore require no migration or backfill.
BODY_AUTHORITY_DERIVED = "derived"
BODY_AUTHORITY_MARKDOWN = "markdown"

BodyAuthority = Literal["derived", "markdown"]
BodyWriteIntent = Literal[
    "authoritative_input",
    "derived_summary_replace",
    "derived_description_append",
]


def normalize_body_authority(value: Any) -> BodyAuthority:
    """Normalize stored/user-provided authority values to a supported enum."""
    if isinstance(value, str) and value.strip().lower() == BODY_AUTHORITY_MARKDOWN:
        return BODY_AUTHORITY_MARKDOWN
    return BODY_AUTHORITY_DERIVED


def resolve_body_authority(tags: Mapping[str, Any] | None) -> BodyAuthority:
    """Read the effective body authority from a note tag map."""
    if not tags:
        return BODY_AUTHORITY_DERIVED
    return normalize_body_authority(tags.get(BODY_AUTHORITY_TAG))


def body_write_allowed(
    authority: BodyAuthority | Mapping[str, Any] | None,
    intent: BodyWriteIntent,
) -> bool:
    """Return whether a body write with ``intent`` may update stored body text."""
    if not isinstance(authority, str):
        authority = resolve_body_authority(authority)
    if authority == BODY_AUTHORITY_MARKDOWN and intent != "authoritative_input":
        return False
    return True
