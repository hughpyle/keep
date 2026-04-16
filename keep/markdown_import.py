"""Helpers for importing recursive markdown sources into keep documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Callable

import yaml

from .markdown_export import export_ref_from_rel_path
from .markdown_frontmatter import (
    MARKDOWN_EXPORTER_OWNED_KEYS,
    MARKDOWN_FRONTMATTER_ID_KEY,
    MARKDOWN_FRONTMATTER_TAGS_KEY,
)
from .types import normalize_id


_SIDECAR_PART_RE = re.compile(r"^@P\{(\d+)\}\.md$", re.IGNORECASE)
_SIDECAR_VERSION_RE = re.compile(r"^@V\{(\d+)\}\.md$", re.IGNORECASE)
_TIMESTAMP_KEYS = {
    "_created": "created_at",
    "_updated": "updated_at",
    "_accessed": "accessed_at",
}
_IMPORT_RESERVED_KEYS = frozenset(
    set(MARKDOWN_EXPORTER_OWNED_KEYS) | set(_TIMESTAMP_KEYS.keys())
)


@dataclass
class MarkdownImportRecord:
    """Parsed markdown note file ready to be grouped into import documents."""

    rel_path: Path
    doc_id: str
    summary: str
    tags: dict[str, str | list[str]]
    created_at: str | None = None
    updated_at: str | None = None
    accessed_at: str | None = None
    content_hash: str | None = None
    content_hash_full: str | None = None
    part_num: int | None = None
    version: int | None = None
    version_offset: int | None = None

    @property
    def export_ref(self) -> str:
        return export_ref_from_rel_path(self.rel_path)

    @property
    def canonical_ref_id(self) -> str:
        if self.part_num is not None:
            return f"{self.doc_id}@P{{{self.part_num}}}"
        if self.version_offset is not None:
            return f"{self.doc_id}@V{{{self.version_offset}}}"
        return self.doc_id


def _load_yaml_frontmatter(content: str) -> tuple[str, dict[str, Any]]:
    if not content.startswith("---"):
        return content, {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return content, {}

    try:
        frontmatter = yaml.safe_load(parts[1])
    except Exception:
        return content, {}

    body = parts[2].lstrip("\n")
    if not isinstance(frontmatter, dict):
        return body, {}
    return body, frontmatter


def _coerce_scalar(value: Any) -> str | None:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return None


def _coerce_tag_value(value: Any) -> str | list[str] | None:
    scalar = _coerce_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, list):
        vals = [sv for item in value if (sv := _coerce_scalar(item)) is not None]
        if vals:
            return vals
    return None


def _parse_import_record(root: Path, path: Path) -> MarkdownImportRecord:
    content = path.read_text(encoding="utf-8")
    body, frontmatter = _load_yaml_frontmatter(content)
    rel_path = path.relative_to(root)

    tags: dict[str, str | list[str]] = {}
    metadata: dict[str, Any] = {}

    for key, value in frontmatter.items():
        key_str = str(key)
        if key_str == MARKDOWN_FRONTMATTER_ID_KEY:
            metadata["doc_id"] = str(value)
            continue
        if key_str in _TIMESTAMP_KEYS:
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata[_TIMESTAMP_KEYS[key_str]] = scalar
            continue
        if key_str == "_content_hash":
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata["content_hash"] = scalar
            continue
        if key_str == "_content_hash_full":
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata["content_hash_full"] = scalar
            continue
        if key_str == "_part_num":
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata["part_num"] = int(scalar)
            continue
        if key_str == "_version":
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata["version"] = int(scalar)
            continue
        if key_str == "_version_offset":
            scalar = _coerce_scalar(value)
            if scalar is not None:
                metadata["version_offset"] = int(scalar)
            continue
        if key_str == MARKDOWN_FRONTMATTER_TAGS_KEY and isinstance(value, dict):
            for tag_key, tag_value in value.items():
                tag_key_str = str(tag_key)
                if tag_key_str == MARKDOWN_FRONTMATTER_ID_KEY:
                    continue
                if tag_key_str in _IMPORT_RESERVED_KEYS:
                    continue
                coerced = _coerce_tag_value(tag_value)
                if coerced is not None:
                    tags[tag_key_str] = coerced
            continue
        if key_str in _IMPORT_RESERVED_KEYS:
            continue
        coerced = _coerce_tag_value(value)
        if coerced is not None:
            tags[key_str] = coerced

    if "part_num" not in metadata:
        if match := _SIDECAR_PART_RE.match(rel_path.name):
            metadata["part_num"] = int(match.group(1))
    if "version_offset" not in metadata:
        if match := _SIDECAR_VERSION_RE.match(rel_path.name):
            metadata["version_offset"] = int(match.group(1))
            metadata.setdefault("version", int(match.group(1)))

    default_id = export_ref_from_rel_path(rel_path)
    doc_id = normalize_id(str(metadata.pop("doc_id", default_id)))
    return MarkdownImportRecord(
        rel_path=rel_path,
        doc_id=doc_id,
        summary=body,
        tags=tags,
        created_at=metadata.get("created_at"),
        updated_at=metadata.get("updated_at"),
        accessed_at=metadata.get("accessed_at"),
        content_hash=metadata.get("content_hash"),
        content_hash_full=metadata.get("content_hash_full"),
        part_num=metadata.get("part_num"),
        version=metadata.get("version"),
        version_offset=metadata.get("version_offset"),
    )


def _discover_markdown_files(source: str | Path) -> tuple[Path, Path, list[Path]]:
    """Return ``(src_path, root, files)`` for a markdown import source."""
    src_path = Path(source)
    if not src_path.exists():
        raise FileNotFoundError(str(src_path))

    if src_path.is_dir():
        root = src_path
        files = sorted(p for p in src_path.rglob("*.md") if p.is_file())
    else:
        root = src_path.parent
        files = [src_path]

    if not files:
        raise ValueError(f"No markdown files found in {src_path}")
    return src_path, root, files


def count_markdown_import_files(source: str | Path) -> int:
    """Return how many markdown files would be imported from ``source``."""
    _, _, files = _discover_markdown_files(source)
    return len(files)


def load_markdown_import(
    source: str | Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Load one markdown file or a directory tree into keep import docs."""
    _src_path, root, files = _discover_markdown_files(source)
    parsed: list[MarkdownImportRecord] = []
    total = len(files)
    for idx, path in enumerate(files, start=1):
        record = _parse_import_record(root, path)
        parsed.append(record)
        if progress is not None:
            progress(idx, total, record.rel_path.as_posix())
    ref_map = {record.export_ref: record.canonical_ref_id for record in parsed}

    now_iso = datetime.now(timezone.utc).isoformat()
    grouped: dict[str, dict[str, Any]] = {}

    for record in parsed:
        doc = grouped.setdefault(
            record.doc_id,
            {
                "id": record.doc_id,
                "summary": "",
                "tags": {},
                "created_at": record.created_at or record.updated_at or now_iso,
                "updated_at": record.updated_at or record.created_at or now_iso,
                "accessed_at": record.accessed_at or record.updated_at or record.created_at or now_iso,
                "versions": [],
                "parts": [],
            },
        )

        if record.part_num is not None:
            doc["parts"].append({
                "part_num": record.part_num,
                "summary": record.summary,
                "tags": record.tags,
                "created_at": record.created_at or record.updated_at or now_iso,
            })
            continue

        if record.version_offset is not None:
            doc["versions"].append({
                "version": record.version or record.version_offset,
                "summary": record.summary,
                "tags": record.tags,
                "content_hash": record.content_hash,
                "created_at": record.created_at or record.updated_at or now_iso,
            })
            continue

        doc["summary"] = record.summary
        doc["tags"] = record.tags
        doc["created_at"] = record.created_at or doc["created_at"]
        doc["updated_at"] = record.updated_at or record.created_at or doc["updated_at"]
        doc["accessed_at"] = (
            record.accessed_at
            or record.updated_at
            or record.created_at
            or doc["accessed_at"]
        )
        if record.content_hash is not None:
            doc["content_hash"] = record.content_hash
        if record.content_hash_full is not None:
            doc["content_hash_full"] = record.content_hash_full

    documents: list[dict[str, Any]] = []
    for doc in grouped.values():
        doc["parts"].sort(key=lambda part: int(part["part_num"]))
        doc["versions"].sort(key=lambda version: int(version["version"]))
        if not doc["parts"]:
            doc.pop("parts")
        if not doc["versions"]:
            doc.pop("versions")
        documents.append(doc)

    documents.sort(key=lambda doc: str(doc["id"]))
    return documents, ref_map
