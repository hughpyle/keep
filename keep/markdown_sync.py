"""Shared markdown-sync mutation constants and helpers."""

from __future__ import annotations

import json
from typing import Any


DOC_INSERT_MUTATION = "doc_insert"
DOC_DELETE_MUTATION = "doc_delete"
DOC_UPDATE_MUTATION = "doc_update"

PART_INSERT_MUTATION = "part_insert"
PART_UPDATE_MUTATION = "part_update"
PART_DELETE_MUTATION = "part_delete"

VERSION_INSERT_MUTATION = "version_insert"
VERSION_UPDATE_MUTATION = "version_update"
VERSION_DELETE_MUTATION = "version_delete"

EDGE_INSERT_MUTATION = "edge_insert"
EDGE_UPDATE_MUTATION = "edge_update"
EDGE_DELETE_MUTATION = "edge_delete"

VERSION_EDGE_INSERT_MUTATION = "version_edge_insert"
VERSION_EDGE_UPDATE_MUTATION = "version_edge_update"
VERSION_EDGE_DELETE_MUTATION = "version_edge_delete"

DOC_STRUCTURAL_MUTATIONS = frozenset({
    DOC_INSERT_MUTATION,
    DOC_DELETE_MUTATION,
})
PART_MUTATIONS = frozenset({
    PART_INSERT_MUTATION,
    PART_UPDATE_MUTATION,
    PART_DELETE_MUTATION,
})
VERSION_MUTATIONS = frozenset({
    VERSION_INSERT_MUTATION,
    VERSION_UPDATE_MUTATION,
    VERSION_DELETE_MUTATION,
})
EDGE_MUTATIONS = frozenset({
    EDGE_INSERT_MUTATION,
    EDGE_UPDATE_MUTATION,
    EDGE_DELETE_MUTATION,
})
VERSION_EDGE_MUTATIONS = frozenset({
    VERSION_EDGE_INSERT_MUTATION,
    VERSION_EDGE_UPDATE_MUTATION,
    VERSION_EDGE_DELETE_MUTATION,
})


def decode_sync_event_payload(value: Any) -> dict[str, Any]:
    """Return a normalized payload dict from a sync event row."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}
