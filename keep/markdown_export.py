"""Markdown export helpers shared by CLI and daemon-owned mirrors."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, runtime_checkable
from urllib.parse import quote

import yaml

from .dependencies import NoteDependencyService
from .markdown_frontmatter import (
    MARKDOWN_EXPORTER_OWNED_KEYS,
    MARKDOWN_FRONTMATTER_ID_KEY,
)
from .types import format_ref, note_display_name, parse_ref


# Conservative filename length limit.  Most filesystems cap a single
# path component at 255 bytes (ext4, HFS+, APFS, NTFS); we stay under
# that with headroom for unusual encodings.
_MAX_FILENAME_BYTES = 200


@dataclass(frozen=True)
class RenderBundleState:
    """Rendered note-bundle metadata used by markdown export writers."""

    document: dict[str, Any]
    current_inverse: list[tuple[str, str]]
    version_inverse: list[tuple[str, str]]
    is_edge_tag: Callable[[str], bool]


@runtime_checkable
class LocalMarkdownExportHost(Protocol):
    """Host with direct local graph access for markdown export helpers."""

    _document_store: Any

    def _resolve_doc_collection(self) -> str: ...


def _encode_path_component(component: str) -> str:
    """Percent-encode a single path component for filesystem safety."""
    return quote(component, safe="-._~ @+=,()")


def _truncate_component(comp: str, *, budget: int) -> str:
    """Truncate a single path component to ``budget`` bytes, disambiguating."""
    if len(comp.encode("utf-8")) <= budget:
        return comp
    digest = hashlib.sha256(comp.encode("utf-8")).hexdigest()[:12]
    suffix = f".{digest}"
    comp_budget = budget - len(suffix)
    truncated = comp.encode("utf-8")[:comp_budget].decode("utf-8", errors="ignore")
    while truncated.endswith("%") or (
        len(truncated) >= 2 and truncated[-2] == "%"
    ):
        truncated = truncated[:-1]
    return truncated + suffix


_URI_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$", re.DOTALL)
_INLINE_CONTENT_ID_RE = re.compile(r"^%[0-9a-f]{12}$")
_BARE_EMAIL_ID_RE = re.compile(
    r"^(?P<local>[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+)@"
    r"(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$"
)


def _id_to_rel_path(doc_id: str) -> Path:
    """Derive a filesystem-safe relative path (with subdirs) from a doc id."""
    raw_components: list[str]
    if _INLINE_CONTENT_ID_RE.match(doc_id):
        raw_components = ["_inline", doc_id[1:]]
    elif (email_match := _BARE_EMAIL_ID_RE.match(doc_id)):
        raw_components = [
            "_email",
            email_match.group("domain").lower(),
            doc_id,
        ]
    elif (m := _URI_SCHEME_RE.match(doc_id)):
        scheme = m.group(1)
        rest = m.group(2)
        if rest.startswith("//"):
            rest = rest[2:]
        raw_components = [scheme]
        raw_components.extend(c for c in rest.split("/") if c)
    elif "/" in doc_id:
        raw_components = [c for c in doc_id.split("/") if c]
    else:
        raw_components = [doc_id]

    if not raw_components:
        raw_components = [""]

    out: list[str] = []
    last_idx = len(raw_components) - 1
    for i, comp in enumerate(raw_components):
        encoded = _encode_path_component(comp)
        budget = _MAX_FILENAME_BYTES - (3 if i == last_idx else 0)
        out.append(_truncate_component(encoded, budget=budget))
    out[-1] += ".md"
    return Path(*out)


def _md_link_target(target_rel: Path, src_rel: Path) -> str:
    """Build a relative markdown-link URL from one rel-path to another."""
    src_dir = posixpath.dirname(src_rel.as_posix())
    target = target_rel.as_posix()
    rel = posixpath.relpath(target, src_dir or ".")
    parts: list[str] = []
    for comp in rel.split("/"):
        if comp in ("..", "."):
            parts.append(comp)
        else:
            parts.append(quote(comp, safe="@+=,"))
    return "/".join(parts)


def _export_ref_from_rel_path(rel_path: Path) -> str:
    """Return the vault-native wiki target for an exported markdown path."""
    return rel_path.with_suffix("").as_posix()


def _wiki_link_value(target_id: str) -> str:
    """Render a wrapped frontmatter ref.

    This intentionally differs from ``format_ref(id, None)``: chain and export
    references are always wrapped as ``[[target]]``, even without a label.
    """
    return f"[[{target_id}]]"


def _rewrite_export_ref_value(
    value: str,
    export_refs: Mapping[str, str],
) -> str:
    """Rewrite one canonical ref value to the exported vault-local target."""
    target_id, alias = parse_ref(value)
    export_target = export_refs.get(target_id)
    if export_target is None:
        return value
    if alias is not None:
        return format_ref(export_target, alias)
    return _wiki_link_value(export_target)


def _get_edge_data(
    keeper,
    export_refs: Mapping[str, str] | None = None,
) -> tuple[
    Callable[[str], list[tuple[str, str]]],
    Callable[[str], list[tuple[str, str]]],
]:
    """Return ``(current_inverse, version_inverse)`` lookup functions."""
    try:
        doc_coll = keeper._resolve_doc_collection()
        ds = keeper._document_store
        dependencies = NoteDependencyService(ds, doc_coll)
    except AttributeError:
        empty: Callable[[str], list[tuple[str, str]]] = lambda _id: []
        return empty, empty

    formatted_cache: dict[str, str] = {}
    export_refs = export_refs or {}

    def _format_source(source_id: str) -> str:
        cached = formatted_cache.get(source_id)
        if cached is not None:
            return cached
        export_target = export_refs.get(source_id)
        try:
            record = ds.get(doc_coll, source_id)
        except Exception:
            fallback = _wiki_link_value(export_target or source_id)
            formatted_cache[source_id] = fallback
            return fallback
        if record is None:
            fallback = _wiki_link_value(export_target or source_id)
            formatted_cache[source_id] = fallback
            return fallback
        display = note_display_name(record.tags, record.summary or "")
        if display != source_id:
            target = export_target or source_id
            formatted = format_ref(target, display)
        else:
            formatted = _wiki_link_value(export_target or source_id)
        formatted_cache[source_id] = formatted
        return formatted

    def current_inverse(doc_id: str) -> list[tuple[str, str]]:
        return [
            (dep.relationship, _format_source(dep.note_id))
            for dep in dependencies.current_sources(doc_id)
        ]

    def version_inverse(doc_id: str) -> list[tuple[str, str]]:
        return [
            (dep.relationship, _format_source(dep.note_id))
            for dep in dependencies.archived_sources(doc_id)
        ]

    return current_inverse, version_inverse


def _get_export_doc(
    keeper,
    doc_id: str,
) -> dict | None:
    """Return one export-shaped note dict from the current store."""
    # Keep this shape aligned with Keeper.export_iter(). Incremental export
    # needs one-note assembly without rescanning the whole store, so this is a
    # deliberately duplicated single-note form of that export loop.
    try:
        doc_coll = keeper._resolve_doc_collection()
        record = keeper._document_store.get(doc_coll, doc_id)
    except AttributeError:
        return None
    if record is None:
        return None

    doc_dict: dict = {
        "id": record.id,
        "summary": record.summary,
        "tags": dict(record.tags),
        "content_hash": record.content_hash,
        "content_hash_full": record.content_hash_full,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "accessed_at": record.accessed_at,
    }

    versions = []
    for vi in keeper._document_store.list_versions(doc_coll, doc_id, limit=10000):
        versions.append({
            "version": vi.version,
            "summary": vi.summary,
            "tags": dict(vi.tags),
            "content_hash": vi.content_hash,
            "created_at": vi.created_at,
        })
    if versions:
        doc_dict["versions"] = versions

    parts = []
    for pi in keeper._document_store.list_parts(doc_coll, doc_id):
        parts.append({
            "part_num": pi.part_num,
            "summary": pi.summary,
            "tags": dict(pi.tags),
            "created_at": pi.created_at,
        })
    if parts:
        doc_dict["parts"] = parts

    return doc_dict


def _filter_inverse_edges(
    inverse_edges: list[tuple[str, str]],
    *,
    include_system: bool,
) -> list[tuple[str, str]]:
    """Return filtered, deduplicated inverse-edge pairs."""
    filtered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for inverse, source in inverse_edges:
        if not source:
            continue
        bare_id, _alias = parse_ref(source)
        if not include_system and bare_id.startswith("."):
            continue
        key = (inverse, source)
        if key in seen:
            continue
        seen.add(key)
        filtered.append(key)
    return filtered


def _get_export_bundle(
    keeper,
    doc_id: str,
    *,
    include_system: bool = True,
    include_parts: bool = True,
    include_versions: bool = True,
) -> dict | None:
    """Return one export bundle plus rendering metadata for a note."""
    doc = _get_export_doc(keeper, doc_id)
    if doc is None:
        return None
    if not include_parts:
        doc.pop("parts", None)
    if not include_versions:
        doc.pop("versions", None)

    try:
        doc_coll = keeper._resolve_doc_collection()
        ds = keeper._document_store
    except AttributeError:
        return {
            "document": doc,
            "current_inverse": [],
            "version_inverse": [],
            "edge_tag_keys": [],
        }

    current_inverse_lookup, version_inverse_lookup = _get_edge_data(keeper)
    current_inverse = _filter_inverse_edges(
        current_inverse_lookup(doc_id),
        include_system=include_system,
    )
    version_inverse = _filter_inverse_edges(
        version_inverse_lookup(doc_id),
        include_system=include_system,
    ) if include_versions else []

    candidate_keys: set[str] = set()
    for tag_key in doc.get("tags", {}) or {}:
        if not str(tag_key).startswith("_"):
            candidate_keys.add(str(tag_key))
    if include_parts:
        for part in doc.get("parts", []) or []:
            for tag_key in part.get("tags", {}) or {}:
                if not str(tag_key).startswith("_"):
                    candidate_keys.add(str(tag_key))
    if include_versions:
        for version in doc.get("versions", []) or []:
            for tag_key in version.get("tags", {}) or {}:
                if not str(tag_key).startswith("_"):
                    candidate_keys.add(str(tag_key))

    edge_tag_keys: list[str] = []
    for key in sorted(candidate_keys):
        tagdoc = ds.get(doc_coll, f".tag/{key}")
        if tagdoc and tagdoc.tags.get("_inverse"):
            edge_tag_keys.append(key)

    return {
        "document": doc,
        "current_inverse": current_inverse,
        "version_inverse": version_inverse,
        "edge_tag_keys": edge_tag_keys,
    }


def _supports_local_markdown_export_graph(keeper: object) -> bool:
    return isinstance(keeper, LocalMarkdownExportHost)


def _local_edge_tag_resolver(keeper):
    doc_coll = keeper._resolve_doc_collection()
    edge_tag_cache: dict[str, bool] = {}

    def is_edge_tag(key: str) -> bool:
        if key.startswith("_"):
            return False
        cached = edge_tag_cache.get(key)
        if cached is not None:
            return cached
        tagdoc = keeper._document_store.get(doc_coll, f".tag/{key}")
        is_edge = bool(tagdoc and tagdoc.tags.get("_inverse"))
        edge_tag_cache[key] = is_edge
        return is_edge

    return is_edge_tag


def _normalize_bundle_inverse_edges(value: Any) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    if not isinstance(value, list):
        return normalized
    for edge in value:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            continue
        predicate, source = edge
        normalized.append((str(predicate), str(source)))
    return normalized


def _rewrite_export_refs_in_inverse_edges(
    inverse_edges: list[tuple[str, str]],
    *,
    export_refs: Mapping[str, str],
) -> list[tuple[str, str]]:
    rewritten: list[tuple[str, str]] = []
    for predicate, source in inverse_edges:
        rewritten.append(
            (predicate, _rewrite_export_ref_value(source, export_refs)),
        )
    return rewritten


def _bundle_edge_tag_resolver(bundle: Mapping[str, Any]):
    edge_tag_keys = {
        str(key)
        for key in bundle.get("edge_tag_keys", [])
        if isinstance(key, str) and not key.startswith("_")
    }

    def is_edge_tag(key: str) -> bool:
        return key in edge_tag_keys

    return is_edge_tag


def resolve_remote_render_bundle(
    bundle: Mapping[str, Any],
    *,
    export_refs: Mapping[str, str],
    fallback_document: Mapping[str, Any] | None = None,
) -> RenderBundleState:
    """Normalize a remote note bundle into markdown rendering inputs."""
    render_doc = dict(fallback_document or {})
    bundled_doc = bundle.get("document")
    if isinstance(bundled_doc, dict):
        render_doc = bundled_doc
    current_inverse = normalize_bundle_inverse_edges(
        bundle.get("current_inverse"),
    )
    version_inverse = normalize_bundle_inverse_edges(
        bundle.get("version_inverse"),
    )
    current_inverse = rewrite_export_refs_in_inverse_edges(
        current_inverse,
        export_refs=export_refs,
    )
    version_inverse = rewrite_export_refs_in_inverse_edges(
        version_inverse,
        export_refs=export_refs,
    )
    return RenderBundleState(
        document=render_doc,
        current_inverse=current_inverse,
        version_inverse=version_inverse,
        is_edge_tag=bundle_edge_tag_resolver(bundle),
    )


def _group_inverse_edges_to_tags(
    inverse_edges: list[tuple[str, str]],
    *,
    include_system: bool,
) -> dict[str, list[str]]:
    """Group inverse edges by predicate for frontmatter rendering."""
    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for inverse, source in inverse_edges:
        if not source:
            continue
        bare_id, _alias = parse_ref(source)
        if not include_system and bare_id.startswith("."):
            continue
        key = (inverse, source)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault(inverse, []).append(source)
    return groups


def _merge_inverse_edges_into_tags(
    tags: dict[str, object],
    inverse_edges: Optional[list[tuple[str, str]]],
    *,
    include_system: bool,
) -> dict[str, object]:
    """Merge inverse-edge values into a flat tag map."""
    if not inverse_edges:
        return tags
    groups = _group_inverse_edges_to_tags(
        inverse_edges, include_system=include_system,
    )
    if not groups:
        return tags
    merged: dict[str, object] = dict(tags)
    for predicate, sources in groups.items():
        existing = merged.get(predicate)
        if existing is None:
            merged[predicate] = sources
        elif isinstance(existing, list):
            seen = set(existing)
            extra = [s for s in sources if s not in seen]
            merged[predicate] = existing + extra
        else:
            extras = [s for s in sources if s != existing]
            merged[predicate] = [existing, *extras]
    return merged


class _ExportYamlDumper(yaml.SafeDumper):
    """SafeDumper variant that double-quotes labeled-ref strings."""


_YAML_NO_WRAP = 1_000_000_000


def _wikilink_str_representer(dumper: yaml.SafeDumper, data: str):
    if "[[" in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style='"',
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_ExportYamlDumper.add_representer(str, _wikilink_str_representer)


_EXPORT_META_ID = MARKDOWN_FRONTMATTER_ID_KEY
_EXPORT_META_CONTENT_HASH = "_content_hash"
_EXPORT_META_CONTENT_HASH_FULL = "_content_hash_full"
_EXPORT_META_CREATED = "_created"
_EXPORT_META_PART_NUM = "_part_num"
_EXPORT_META_VERSION = "_version"
_EXPORT_META_VERSION_OFFSET = "_version_offset"
_EXPORT_RESERVED_KEYS = MARKDOWN_EXPORTER_OWNED_KEYS


def _render_frontmatter_markdown(meta: dict, body: str) -> str:
    """Wrap a meta dict and body string in YAML frontmatter + markdown body."""
    fm = yaml.dump(
        meta,
        Dumper=_ExportYamlDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=_YAML_NO_WRAP,
    )
    text = f"---\n{fm}---\n\n{body}"
    if not text.endswith("\n"):
        text += "\n"
    return text


def _rewrite_export_refs_in_tags(
    tags: dict[str, object],
    *,
    export_refs: Mapping[str, str],
    is_edge_tag: Callable[[str], bool] | None = None,
) -> dict[str, object]:
    """Rewrite stored edge-tag values to exported vault-native refs."""
    if not tags or not export_refs or is_edge_tag is None:
        return tags

    rewritten: dict[str, object] = {}
    for tag_key, tag_value in tags.items():
        if tag_key.startswith("_") or not is_edge_tag(tag_key):
            rewritten[tag_key] = tag_value
            continue
        if isinstance(tag_value, list):
            rewritten[tag_key] = [
                _rewrite_export_ref_value(v, export_refs)
                if isinstance(v, str) else v
                for v in tag_value
            ]
        elif isinstance(tag_value, str):
            rewritten[tag_key] = _rewrite_export_ref_value(tag_value, export_refs)
        else:
            rewritten[tag_key] = tag_value
    return rewritten


def _render_doc_markdown(
    doc: dict,
    *,
    next_part_id: Optional[str] = None,
    prev_version_id: Optional[str] = None,
    inverse_edges: Optional[list[tuple[str, str]]] = None,
    include_system: bool = True,
    export_refs: Mapping[str, str] | None = None,
    is_edge_tag: Callable[[str], bool] | None = None,
) -> str:
    """Render a single exported document dict as markdown with YAML frontmatter."""
    meta: dict = {_EXPORT_META_ID: doc["id"]}
    if doc.get("content_hash") is not None:
        meta[_EXPORT_META_CONTENT_HASH] = doc["content_hash"]
    if doc.get("content_hash_full") is not None:
        meta[_EXPORT_META_CONTENT_HASH_FULL] = doc["content_hash_full"]

    if prev_version_id is not None:
        meta["_prev_version"] = _wiki_link_value(prev_version_id)
    if next_part_id is not None:
        meta["_next_part"] = _wiki_link_value(next_part_id)

    tags = dict(doc.get("tags") or {})
    if doc.get("created_at") and "_created" not in tags:
        tags["_created"] = doc["created_at"]
    if doc.get("updated_at") and "_updated" not in tags:
        tags["_updated"] = doc["updated_at"]
    if doc.get("accessed_at") and "_accessed" not in tags:
        tags["_accessed"] = doc["accessed_at"]

    tags = _rewrite_export_refs_in_tags(
        tags,
        export_refs=export_refs or {},
        is_edge_tag=is_edge_tag,
    )
    tags = _merge_inverse_edges_into_tags(
        tags, inverse_edges, include_system=include_system,
    )
    for tag_key, tag_value in tags.items():
        meta[tag_key] = tag_value

    body = doc.get("summary", "") or ""
    return _render_frontmatter_markdown(meta, body)


def _render_part_markdown(
    parent_id: str,
    part: dict,
    *,
    prev_part_id: Optional[str] = None,
    next_part_id: Optional[str] = None,
    export_refs: Mapping[str, str] | None = None,
    is_edge_tag: Callable[[str], bool] | None = None,
) -> str:
    """Render a single analysis part as markdown with YAML frontmatter."""
    meta: dict = {_EXPORT_META_ID: parent_id}
    if prev_part_id is not None:
        meta["_prev_part"] = _wiki_link_value(prev_part_id)
    if next_part_id is not None:
        meta["_next_part"] = _wiki_link_value(next_part_id)
    part_tags = dict(part.get("tags") or {})
    part_tags = _rewrite_export_refs_in_tags(
        part_tags,
        export_refs=export_refs or {},
        is_edge_tag=is_edge_tag,
    )
    if part.get("created_at") and _EXPORT_META_CREATED not in part_tags:
        meta[_EXPORT_META_CREATED] = part["created_at"]
    for tag_key, tag_value in part_tags.items():
        if tag_key in _EXPORT_RESERVED_KEYS:
            continue
        meta[tag_key] = tag_value
    meta[_EXPORT_META_PART_NUM] = part["part_num"]

    body = part.get("summary", "") or ""
    return _render_frontmatter_markdown(meta, body)


def _render_version_markdown(
    parent_id: str,
    version: dict,
    offset: int,
    *,
    prev_version_id: Optional[str] = None,
    next_version_id: Optional[str] = None,
    inverse_edges: Optional[list[tuple[str, str]]] = None,
    include_system: bool = True,
    export_refs: Mapping[str, str] | None = None,
    is_edge_tag: Callable[[str], bool] | None = None,
) -> str:
    """Render an archived version as markdown with YAML frontmatter."""
    meta: dict = {_EXPORT_META_ID: parent_id}
    if version.get("content_hash") is not None:
        meta[_EXPORT_META_CONTENT_HASH] = version["content_hash"]

    if prev_version_id is not None:
        meta["_prev_version"] = _wiki_link_value(prev_version_id)
    if next_version_id is not None:
        meta["_next_version"] = _wiki_link_value(next_version_id)

    version_tags = dict(version.get("tags") or {})
    version_tags = _rewrite_export_refs_in_tags(
        version_tags,
        export_refs=export_refs or {},
        is_edge_tag=is_edge_tag,
    )
    if version.get("created_at") and _EXPORT_META_CREATED not in version_tags:
        meta[_EXPORT_META_CREATED] = version["created_at"]

    version_tags = _merge_inverse_edges_into_tags(
        version_tags, inverse_edges, include_system=include_system,
    )
    for tag_key, tag_value in version_tags.items():
        if tag_key in _EXPORT_RESERVED_KEYS:
            continue
        meta[tag_key] = tag_value
    meta[_EXPORT_META_VERSION_OFFSET] = offset
    meta[_EXPORT_META_VERSION] = version["version"]

    body = version.get("summary", "") or ""
    return _render_frontmatter_markdown(meta, body)


class _ExportCollisionError(Exception):
    """Raised when two distinct ids would write to the same export path."""

    def __init__(self, path: Path, first_id: str, second_id: str) -> None:
        self.path = path
        self.first_id = first_id
        self.second_id = second_id
        super().__init__(
            f"id collision: '{first_id}' and '{second_id}' both map to '{path}'"
        )


def _planned_slots(
    rel_path: Path,
    sorted_parts: list[dict],
    version_offsets: list[int],
) -> list[tuple[Path, str]]:
    """Return every (path, kind) slot one doc's writes would occupy."""
    slots_list: list[tuple[Path, str]] = []
    ancestors = rel_path.parts[:-1]
    for i in range(1, len(ancestors) + 1):
        slots_list.append((Path(*ancestors[:i]), "dir"))
    slots_list.append((rel_path, "file"))

    if sorted_parts or version_offsets:
        sidecar_dir = rel_path.with_suffix("")
        slots_list.append((sidecar_dir, "dir"))
        for p in sorted_parts:
            pnum = p["part_num"]
            slots_list.append((sidecar_dir / f"@P{{{pnum}}}.md", "file"))
        for offset in version_offsets:
            slots_list.append((sidecar_dir / f"@V{{{offset}}}.md", "file"))
    return slots_list


def _bundle_export_refs(
    doc: dict,
    rel_path: Path,
    *,
    include_parts: bool,
    include_versions: bool,
) -> dict[str, str]:
    """Return keep-id -> export-ref entries for one note bundle."""
    doc_id = doc["id"]
    refs = {doc_id: _export_ref_from_rel_path(rel_path)}
    sidecar_dir = rel_path.with_suffix("")
    if include_parts:
        for part in sorted(doc.get("parts") or [], key=lambda p: p["part_num"]):
            pnum = part["part_num"]
            export_ref = _export_ref_from_rel_path(sidecar_dir / f"@P{{{pnum}}}.md")
            refs[f"{doc_id}@P{{{pnum}}}"] = export_ref
            refs[f"{doc_id}@p{pnum}"] = export_ref
    if include_versions:
        for offset, _version in enumerate(doc.get("versions") or [], start=1):
            refs[f"{doc_id}@V{{{offset}}}"] = _export_ref_from_rel_path(
                sidecar_dir / f"@V{{{offset}}}.md",
            )
    return refs


def _render_doc_bundle(
    keeper,
    doc: dict,
    rel_path: Path,
    *,
    include_system: bool,
    include_parts: bool,
    include_versions: bool,
    export_refs: Mapping[str, str],
    current_inverse: Callable[[str], list[tuple[str, str]]],
    version_inverse: Callable[[str], list[tuple[str, str]]],
    is_edge_tag: Callable[[str], bool],
) -> dict[Path, str]:
    """Render one note's file set: note plus any exported sidecars."""
    doc_id = doc["id"]
    files: dict[Path, str] = {}
    sidecar_dir = rel_path.with_suffix("")

    sorted_parts = sorted(
        doc.get("parts") or [], key=lambda p: p["part_num"],
    ) if include_parts else []
    parts_chain: list[tuple[int, str, Path, str]] = []
    for part in sorted_parts:
        pnum = part["part_num"]
        sidecar_rel = sidecar_dir / f"@P{{{pnum}}}.md"
        parts_chain.append(
            (
                pnum,
                f"{doc_id}@P{{{pnum}}}",
                sidecar_rel,
                _export_ref_from_rel_path(sidecar_rel),
            )
        )

    versions = list(doc.get("versions") or []) if include_versions else []
    versions_chain: list[tuple[int, str, Path, str]] = []
    for offset, _version in enumerate(versions, start=1):
        sidecar_rel = sidecar_dir / f"@V{{{offset}}}.md"
        versions_chain.append(
            (
                offset,
                f"{doc_id}@V{{{offset}}}",
                sidecar_rel,
                _export_ref_from_rel_path(sidecar_rel),
            )
        )

    first_part_id: Optional[str] = None
    if parts_chain:
        first_part_id = parts_chain[0][3]

    first_version_id: Optional[str] = None
    if versions_chain:
        first_version_id = versions_chain[0][3]

    files[rel_path] = _render_doc_markdown(
        doc,
        next_part_id=first_part_id,
        prev_version_id=first_version_id,
        inverse_edges=current_inverse(doc_id),
        include_system=include_system,
        export_refs=export_refs,
        is_edge_tag=is_edge_tag,
    )

    if include_parts and sorted_parts:
        n = len(parts_chain)
        for idx, part in enumerate(sorted_parts):
            if idx == 0:
                prev_part_id = export_refs[doc_id]
            else:
                prev_part_id = parts_chain[idx - 1][3]

            next_part_id: Optional[str] = None
            if idx + 1 < n:
                next_part_id = parts_chain[idx + 1][3]

            files[parts_chain[idx][2]] = _render_part_markdown(
                doc_id,
                part,
                prev_part_id=prev_part_id,
                next_part_id=next_part_id,
                export_refs=export_refs,
                is_edge_tag=is_edge_tag,
            )

    if include_versions and versions_chain:
        n = len(versions_chain)
        historical_inverse = version_inverse(doc_id)
        for idx, version in enumerate(versions):
            if idx == 0:
                next_version_id = export_refs[doc_id]
            else:
                next_version_id = versions_chain[idx - 1][3]

            prev_version_id: Optional[str] = None
            if idx + 1 < n:
                prev_version_id = versions_chain[idx + 1][3]

            files[versions_chain[idx][2]] = _render_version_markdown(
                doc_id,
                version,
                versions_chain[idx][0],
                prev_version_id=prev_version_id,
                next_version_id=next_version_id,
                inverse_edges=historical_inverse,
                include_system=include_system,
                export_refs=export_refs,
                is_edge_tag=is_edge_tag,
            )

    return files


def _find_slot_conflict(
    planned: list[tuple[Path, str]],
    slots: dict[str, tuple[str, str, Path]],
) -> Optional[tuple[str, Path]]:
    """Return ``(existing_owner, existing_path)`` of the first conflict."""
    for path, kind in planned:
        key = path.as_posix().casefold()
        existing = slots.get(key)
        if existing is None:
            continue
        existing_kind, existing_owner, existing_path = existing
        if kind == "dir" and existing_kind == "dir":
            continue
        return existing_owner, existing_path
    return None


def _claim_planned(
    planned: list[tuple[Path, str]],
    owner_id: str,
    slots: dict[str, tuple[str, str, Path]],
) -> None:
    """Insert all planned slots into ``slots``."""
    for path, kind in planned:
        key = path.as_posix().casefold()
        existing = slots.get(key)
        if existing is None:
            slots[key] = (kind, owner_id, path)
            continue
        if kind == "dir" and existing[0] == "dir":
            continue


def _disambiguate_rel_path(canonical: Path, doc_id: str) -> Path:
    """Append a short SHA256 hash to the stem of ``canonical``."""
    digest = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:8]
    new_stem = f"{canonical.stem}.{digest}"
    return canonical.with_name(f"{new_stem}{canonical.suffix}")


def _write_markdown_export(
    keeper,
    out_dir: Path,
    *,
    include_system: bool,
    include_parts: bool = False,
    include_versions: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
    export_map: dict[str, str] | None = None,
    written_paths: set[Path] | None = None,
) -> tuple[int, dict]:
    """Write a markdown-per-note export into ``out_dir``."""
    it = keeper.export_iter(include_system=include_system)
    header = next(it)
    total = header["store_info"]["document_count"]

    slots: dict[str, tuple[str, str, Path]] = {}
    final_paths: dict[str, Path] = {}
    part_nums_by_id: dict[str, list[int]] = {}
    version_offsets_by_id: dict[str, list[int]] = {}

    for doc in it:
        if not isinstance(doc, dict):
            continue
        doc_id = doc["id"]
        canonical = _id_to_rel_path(doc_id)
        sorted_parts = sorted(
            doc.get("parts") or [], key=lambda p: p["part_num"],
        ) if include_parts else []
        version_offsets = list(
            range(1, len(doc.get("versions") or []) + 1)
        ) if include_versions else []
        part_nums_by_id[doc_id] = [part["part_num"] for part in sorted_parts]
        version_offsets_by_id[doc_id] = version_offsets

        planned = _planned_slots(canonical, sorted_parts, version_offsets)
        conflict = _find_slot_conflict(planned, slots)
        if conflict is not None:
            disambiguated = _disambiguate_rel_path(canonical, doc_id)
            planned = _planned_slots(
                disambiguated, sorted_parts, version_offsets,
            )
            second_conflict = _find_slot_conflict(planned, slots)
            if second_conflict is not None:
                existing_owner, existing_path = conflict
                raise _ExportCollisionError(
                    existing_path, existing_owner, doc_id,
                )
            final_path = disambiguated
        else:
            final_path = canonical

        _claim_planned(planned, doc_id, slots)
        final_paths[doc_id] = final_path

    del slots

    export_refs: dict[str, str] = {}
    for doc_id, rel_path in final_paths.items():
        export_refs[doc_id] = _export_ref_from_rel_path(rel_path)
        sidecar_dir = rel_path.with_suffix("")
        for pnum in part_nums_by_id.get(doc_id, []):
            export_ref = _export_ref_from_rel_path(
                sidecar_dir / f"@P{{{pnum}}}.md",
            )
            export_refs[f"{doc_id}@P{{{pnum}}}"] = export_ref
            export_refs[f"{doc_id}@p{pnum}"] = export_ref
        for offset in version_offsets_by_id.get(doc_id, []):
            export_refs[f"{doc_id}@V{{{offset}}}"] = _export_ref_from_rel_path(
                sidecar_dir / f"@V{{{offset}}}.md",
            )

    local_graph = supports_local_markdown_export_graph(keeper)
    if local_graph:
        current_inverse_lookup, version_inverse_lookup = get_edge_data(
            keeper, export_refs=export_refs,
        )
        local_is_edge_tag = local_edge_tag_resolver(keeper)
    elif not hasattr(keeper, "export_bundle"):
        raise ValueError(
            "markdown export requires a host with either local graph access "
            "or export_bundle() support"
        )

    def write(rel_path: Path, text: str) -> None:
        dest = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    count = 0
    it2 = keeper.export_iter(include_system=include_system)
    next(it2)
    for doc in it2:
        if not isinstance(doc, dict):
            continue
        doc_id = doc["id"]
        rel_path = final_paths[doc_id]
        render_doc = doc
        if local_graph:
            current_inverse = current_inverse_lookup(doc_id)
            version_inverse = version_inverse_lookup(doc_id)
            is_edge_tag = local_is_edge_tag
        else:
            bundle = keeper.export_bundle(
                doc_id,
                include_system=include_system,
                include_parts=include_parts,
                include_versions=include_versions,
            )
            if not isinstance(bundle, dict):
                raise ValueError(f"markdown export missing note bundle for {doc_id}")
            remote_bundle = resolve_remote_render_bundle(
                bundle,
                export_refs=export_refs,
                fallback_document=doc,
            )
            render_doc = remote_bundle.document
            current_inverse = remote_bundle.current_inverse
            version_inverse = remote_bundle.version_inverse
            is_edge_tag = remote_bundle.is_edge_tag

        bundle_refs = bundle_export_refs(
            render_doc,
            rel_path,
            include_parts=include_parts,
            include_versions=include_versions,
        )
        bundle_files = render_doc_bundle(
            keeper,
            render_doc,
            rel_path,
            include_system=include_system,
            include_parts=include_parts,
            include_versions=include_versions,
            export_refs=export_refs,
            current_inverse=lambda _doc_id, edges=current_inverse: edges,
            version_inverse=lambda _doc_id, edges=version_inverse: edges,
            is_edge_tag=is_edge_tag,
        )
        for bundle_rel, text in bundle_files.items():
            write(bundle_rel, text)
        if written_paths is not None:
            written_paths.update(bundle_files)
        if export_map is not None:
            for keep_id, export_ref in bundle_refs.items():
                if keep_id == doc_id or "@P{" in keep_id or "@V{" in keep_id:
                    export_map[export_ref] = keep_id

        count += 1
        if progress is not None:
            progress(count, total, doc_id)
    return count, header["store_info"]


# Public helper surface shared by markdown_mirrors and CLI code.
id_to_rel_path = _id_to_rel_path
export_ref_from_rel_path = _export_ref_from_rel_path
get_edge_data = _get_edge_data
get_export_doc = _get_export_doc
supports_local_markdown_export_graph = _supports_local_markdown_export_graph
local_edge_tag_resolver = _local_edge_tag_resolver
normalize_bundle_inverse_edges = _normalize_bundle_inverse_edges
rewrite_export_refs_in_inverse_edges = _rewrite_export_refs_in_inverse_edges
bundle_edge_tag_resolver = _bundle_edge_tag_resolver
bundle_export_refs = _bundle_export_refs
render_doc_bundle = _render_doc_bundle
write_markdown_export = _write_markdown_export
