"""Daemon-owned markdown mirror registry and export passes."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .dependencies import NoteDependencyService
from .markdown_export import (
    _bundle_export_refs,
    _export_ref_from_rel_path,
    _get_edge_data,
    _get_export_doc,
    _id_to_rel_path,
    _render_doc_bundle,
)
from .processors import _content_hash
from .types import file_uri_to_path
from .watches import _DEFAULT_INTERVAL, _DOC_COLLECTION, parse_duration

if TYPE_CHECKING:
    from .api import Keeper

logger = logging.getLogger(__name__)

_MIRRORS_ID = ".markdown-mirrors"
_SYNC_DIR = ".keep-sync"
_MAP_FILE = "map.tsv"
_STATE_FILE = "state.json"
_SYNC_OUTBOX_POLL_SECONDS = 1.0


@dataclass
class MarkdownMirrorEntry:
    """One daemon-managed markdown mirror root."""

    root: str
    include_system: bool = False
    include_parts: bool = False
    include_versions: bool = False
    interval: str = _DEFAULT_INTERVAL
    enabled: bool = True
    added_at: str = ""
    pending_since: str = ""
    pending_full_replan: bool = False
    pending_note_ids: list[str] = field(default_factory=list)
    last_run: str = ""
    last_error: str = ""

    def is_due(self, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        if not self.pending_since:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            pending = datetime.fromisoformat(self.pending_since)
            if pending.tzinfo is None:
                pending = pending.replace(tzinfo=timezone.utc)
            return now >= pending + parse_duration(self.interval)
        except (ValueError, TypeError):
            return True


@dataclass(frozen=True)
class MarkdownMirrorUpdatePlan:
    """One mirror pass plan derived from sync outbox events."""

    full_replan: bool
    note_ids: tuple[str, ...]


def _mirror_to_dict(entry: MarkdownMirrorEntry) -> dict[str, Any]:
    data = asdict(entry)
    return {k: v for k, v in data.items() if v not in ("", False, _DEFAULT_INTERVAL)}


def _dict_to_mirror(data: dict[str, Any]) -> MarkdownMirrorEntry:
    known = {f.name for f in MarkdownMirrorEntry.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in known}
    return MarkdownMirrorEntry(**filtered)


def _resolve_root(root: str | Path) -> Path:
    raw = str(root)
    if raw.startswith("file://"):
        raw = file_uri_to_path(raw)
    return Path(raw).expanduser().resolve()


def _paths_overlap(a: str | Path, b: str | Path) -> bool:
    pa = _resolve_root(a)
    pb = _resolve_root(b)
    try:
        pa.relative_to(pb)
        return True
    except ValueError:
        pass
    try:
        pb.relative_to(pa)
        return True
    except ValueError:
        return False


def load_markdown_mirrors(keeper: Keeper) -> list[MarkdownMirrorEntry]:
    rec = keeper._document_store.get(_DOC_COLLECTION, _MIRRORS_ID)
    if rec is None or not rec.summary:
        return []
    try:
        items = yaml.safe_load(rec.summary)
    except yaml.YAMLError:
        logger.warning("Failed to parse %s", _MIRRORS_ID)
        return []
    if not isinstance(items, list):
        return []
    return [
        _dict_to_mirror(item)
        for item in items
        if isinstance(item, dict)
    ]


def save_markdown_mirrors(keeper: Keeper, entries: list[MarkdownMirrorEntry]) -> None:
    from .types import utc_now as _utc_now

    payload = [_mirror_to_dict(entry) for entry in entries]
    content = yaml.safe_dump(payload, default_flow_style=False) if payload else ""
    now_ts = _utc_now()
    tags = {
        "category": "system",
        "_source": "inline",
        "_updated": now_ts,
    }
    keeper._document_store.upsert(
        collection=_DOC_COLLECTION,
        id=_MIRRORS_ID,
        summary=content,
        tags=tags,
        content_hash=_content_hash(content),
        archive=False,
    )


def list_markdown_mirrors(keeper: Keeper) -> list[MarkdownMirrorEntry]:
    return load_markdown_mirrors(keeper)


def has_active_markdown_mirrors(keeper: Keeper) -> bool:
    return any(entry.enabled for entry in load_markdown_mirrors(keeper))


def next_markdown_mirror_delay(entries: list[MarkdownMirrorEntry]) -> float:
    if not entries:
        return 30.0
    now = datetime.now(timezone.utc)
    min_delay = _SYNC_OUTBOX_POLL_SECONDS
    for entry in entries:
        if not entry.enabled:
            continue
        if not entry.pending_since:
            continue
        try:
            pending = datetime.fromisoformat(entry.pending_since)
            if pending.tzinfo is None:
                pending = pending.replace(tzinfo=timezone.utc)
            due_at = pending + parse_duration(entry.interval or _DEFAULT_INTERVAL)
            min_delay = min(min_delay, (due_at - now).total_seconds())
        except (ValueError, TypeError):
            return 0.0
    return max(0.0, min_delay)


def record_markdown_mirror_export_success(
    keeper: Keeper,
    root: str | Path,
    *,
    error: str = "",
) -> bool:
    from .types import utc_now as _utc_now

    resolved_root = str(_resolve_root(root))
    entries = load_markdown_mirrors(keeper)
    changed = False
    now_ts = _utc_now()
    for entry in entries:
        if entry.root != resolved_root:
            continue
        entry.last_run = now_ts
        entry.pending_since = ""
        entry.pending_full_replan = False
        entry.pending_note_ids = []
        entry.last_error = error
        changed = True
        break
    if changed:
        save_markdown_mirrors(keeper, entries)
    return changed


def path_inside_markdown_mirror(keeper: Keeper, path: str | Path) -> bool:
    candidate = _resolve_root(path)
    return any(
        _paths_overlap(candidate, entry.root)
        for entry in load_markdown_mirrors(keeper)
        if entry.enabled
    )


def add_markdown_mirror(
    keeper: Keeper,
    root: str | Path,
    *,
    include_system: bool = False,
    include_parts: bool = False,
    include_versions: bool = False,
    interval: str = _DEFAULT_INTERVAL,
) -> MarkdownMirrorEntry:
    from .types import utc_now as _utc_now
    from .watches import load_watches

    parse_duration(interval)
    resolved_root = str(_resolve_root(root))
    entries = load_markdown_mirrors(keeper)

    for watch in load_watches(keeper):
        if watch.kind not in ("file", "directory"):
            continue
        if _paths_overlap(resolved_root, watch.source):
            raise ValueError(
                f"Markdown sync root overlaps watched source: {watch.source}"
            )

    for entry in entries:
        if _paths_overlap(resolved_root, entry.root):
            if entry.root == resolved_root:
                entry.include_system = include_system
                entry.include_parts = include_parts
                entry.include_versions = include_versions
                entry.interval = interval
                entry.enabled = True
                save_markdown_mirrors(keeper, entries)
                return entry
            raise ValueError(
                f"Markdown sync root overlaps existing mirror root: {entry.root}"
            )

    entry = MarkdownMirrorEntry(
        root=resolved_root,
        include_system=include_system,
        include_parts=include_parts,
        include_versions=include_versions,
        interval=interval,
        enabled=True,
        added_at=_utc_now(),
    )
    entries.append(entry)
    save_markdown_mirrors(keeper, entries)
    return entry


def remove_markdown_mirror(keeper: Keeper, root: str | Path) -> bool:
    resolved_root = str(_resolve_root(root))
    entries = load_markdown_mirrors(keeper)
    kept = [entry for entry in entries if entry.root != resolved_root]
    if len(kept) == len(entries):
        return False
    save_markdown_mirrors(keeper, kept)
    return True


def _sync_paths(root: Path) -> tuple[Path, Path, Path]:
    sync_dir = root / _SYNC_DIR
    return sync_dir, sync_dir / _MAP_FILE, sync_dir / _STATE_FILE


def _load_map(root: Path) -> dict[str, str]:
    _sync_dir, map_path, _state_path = _sync_paths(root)
    if not map_path.is_file():
        return {}
    lines = map_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}
    entries: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        try:
            export_ref, keep_id = line.split("\t", 1)
        except ValueError:
            continue
        entries[export_ref] = keep_id
    return entries


def _write_map(root: Path, entries: dict[str, str]) -> None:
    sync_dir, map_path, _state_path = _sync_paths(root)
    sync_dir.mkdir(parents=True, exist_ok=True)
    lines = ["export_ref\tkeep_id"]
    for export_ref, keep_id in sorted(entries.items()):
        lines.append(f"{export_ref}\t{keep_id}")
    map_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_state(
    root: Path,
    *,
    entry: MarkdownMirrorEntry | None,
    count: int,
    info: dict[str, Any],
) -> None:
    from .types import utc_now as _utc_now

    sync_dir, _map_path, state_path = _sync_paths(root)
    sync_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "keep-markdown-sync",
        "version": 1,
        "updated_at": _utc_now(),
        "count": count,
        "store_info": info,
    }
    if entry is not None:
        payload["mirror"] = {
            "root": entry.root,
            "include_system": entry.include_system,
            "include_parts": entry.include_parts,
            "include_versions": entry.include_versions,
            "interval": entry.interval,
            "enabled": entry.enabled,
        }
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _keep_id_from_exported_file(rel_path: Path, text: str) -> str | None:
    import yaml

    if not text.startswith("---\n"):
        return None
    try:
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
    except Exception:
        return None
    if not isinstance(frontmatter, dict):
        return None
    parent_id = frontmatter.get("_id")
    if not isinstance(parent_id, str) or not parent_id:
        return None
    name = rel_path.name
    if name.startswith("@P{") and isinstance(frontmatter.get("_part_num"), int):
        return f"{parent_id}@P{{{frontmatter['_part_num']}}}"
    if name.startswith("@V{") and isinstance(frontmatter.get("_version_offset"), int):
        return f"{parent_id}@V{{{frontmatter['_version_offset']}}}"
    return parent_id


def _prune_empty_dirs(root: Path, start: Path) -> None:
    current = start
    while current != root and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _is_exported_note_id(doc_id: str, *, include_system: bool) -> bool:
    return include_system or not doc_id.startswith(".")


def _map_reverse(entries: dict[str, str]) -> dict[str, str]:
    return {keep_id: export_ref for export_ref, keep_id in entries.items()}


def _bundle_map_entries(
    existing_map: dict[str, str],
    doc_id: str,
) -> dict[str, str]:
    return {
        export_ref: keep_id
        for export_ref, keep_id in existing_map.items()
        if keep_id == doc_id
        or keep_id.startswith(f"{doc_id}@P{{")
        or keep_id.startswith(f"{doc_id}@V{{")
        or keep_id.startswith(f"{doc_id}@p")
    }


def _delete_rel_path(root: Path, rel_path: Path) -> None:
    target = root / rel_path
    if target.exists():
        target.unlink()
        _prune_empty_dirs(root, target.parent)


def _current_store_info(keeper: Keeper, *, include_system: bool) -> dict[str, Any]:
    doc_coll = keeper._resolve_doc_collection()
    doc_ids = keeper._document_store.list_ids(doc_coll)
    if not include_system:
        doc_ids = [doc_id for doc_id in doc_ids if not doc_id.startswith(".")]
    return {
        "document_count": len(doc_ids),
        "version_count": sum(
            keeper._document_store.version_count(doc_coll, doc_id)
            for doc_id in doc_ids
        ),
        "part_count": sum(
            keeper._document_store.part_count(doc_coll, doc_id)
            for doc_id in doc_ids
        ),
        "collection": doc_coll,
    }


def _edge_tag_resolver(keeper: Keeper):
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


def _plan_markdown_mirror_update(
    keeper: Keeper,
    entry: MarkdownMirrorEntry,
    events: list[dict[str, Any]],
) -> MarkdownMirrorUpdatePlan:
    if not events:
        return MarkdownMirrorUpdatePlan(full_replan=True, note_ids=())

    doc_coll = keeper._resolve_doc_collection()
    dependencies = NoteDependencyService(keeper._document_store, doc_coll)
    note_ids: set[str] = set()

    def _maybe_add(doc_id: str) -> None:
        if not doc_id:
            return
        if _is_exported_note_id(doc_id, include_system=entry.include_system):
            note_ids.add(doc_id)

    for row in events:
        if row.get("collection") != _DOC_COLLECTION:
            continue
        mutation = str(row.get("mutation") or "")
        doc_id = str(row.get("entity_id") or "")
        payload = row.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if mutation in {"doc_insert", "doc_delete"}:
            if _is_exported_note_id(doc_id, include_system=entry.include_system):
                return MarkdownMirrorUpdatePlan(full_replan=True, note_ids=())
            continue

        if mutation == "doc_update":
            _maybe_add(doc_id)
            for target_id in dependencies.all_target_ids(doc_id):
                _maybe_add(target_id)
            continue

        if mutation in {"part_insert", "part_update", "part_delete"}:
            _maybe_add(doc_id)
            continue

        if mutation in {"version_insert", "version_update", "version_delete"}:
            _maybe_add(doc_id)
            continue

        if mutation in {"edge_insert", "edge_update", "edge_delete"}:
            _maybe_add(doc_id)
            _maybe_add(str(payload.get("target_id") or ""))
            _maybe_add(str(payload.get("old_target_id") or ""))
            continue

        if mutation in {
            "version_edge_insert",
            "version_edge_update",
            "version_edge_delete",
        }:
            _maybe_add(doc_id)
            _maybe_add(str(payload.get("target_id") or ""))
            _maybe_add(str(payload.get("old_target_id") or ""))
            continue

        return MarkdownMirrorUpdatePlan(full_replan=True, note_ids=())

    return MarkdownMirrorUpdatePlan(
        full_replan=False,
        note_ids=tuple(sorted(note_ids)),
    )


def run_markdown_export_incremental(
    keeper: Keeper,
    root: str | Path,
    *,
    note_ids: list[str],
    include_system: bool,
    include_parts: bool = False,
    include_versions: bool = False,
    mirror_entry: MarkdownMirrorEntry | None = None,
) -> tuple[int, dict[str, Any]]:
    """Rewrite only selected exported note bundles under an existing mirror."""
    out_dir = _resolve_root(root)
    if not out_dir.is_dir():
        raise ValueError(f"{out_dir} does not exist")

    existing_map = _load_map(out_dir)
    reverse_map = _map_reverse(existing_map)
    export_refs = dict(reverse_map)
    is_edge_tag = _edge_tag_resolver(keeper)
    current_inverse, version_inverse = _get_edge_data(
        keeper, export_refs=export_refs,
    )

    updated_entries = dict(existing_map)
    rewritten = 0

    for doc_id in note_ids:
        existing_export_ref = reverse_map.get(doc_id)
        if existing_export_ref is None:
            raise ValueError(f"incremental export missing namespace mapping for {doc_id}")
        rel_path = Path(f"{existing_export_ref}.md")
        doc = _get_export_doc(keeper, doc_id)
        if doc is None:
            raise ValueError(f"incremental export missing note {doc_id}")
        if not _is_exported_note_id(doc_id, include_system=include_system):
            continue

        bundle_refs = _bundle_export_refs(
            doc,
            rel_path,
            include_parts=include_parts,
            include_versions=include_versions,
        )
        export_refs.update(bundle_refs)
        bundle_entries = {
            export_ref: keep_id
            for keep_id, export_ref in bundle_refs.items()
            if keep_id == doc_id or "@P{" in keep_id or "@V{" in keep_id
        }
        files = _render_doc_bundle(
            keeper,
            doc,
            rel_path,
            include_system=include_system,
            include_parts=include_parts,
            include_versions=include_versions,
            export_refs=export_refs,
            current_inverse=current_inverse,
            version_inverse=version_inverse,
            is_edge_tag=is_edge_tag,
        )
        for file_rel, text in files.items():
            dest = out_dir / file_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")

        previous_entries = _bundle_map_entries(updated_entries, doc_id)
        stale_refs = set(previous_entries) - set(bundle_entries)
        for stale_ref in stale_refs:
            _delete_rel_path(out_dir, Path(f"{stale_ref}.md"))
            updated_entries.pop(stale_ref, None)

        for old_ref in previous_entries:
            updated_entries.pop(old_ref, None)
        updated_entries.update(bundle_entries)
        rewritten += 1

    _write_map(out_dir, updated_entries)
    info = _current_store_info(keeper, include_system=include_system)
    _write_state(out_dir, entry=mirror_entry, count=info["document_count"], info=info)
    return rewritten, info


def run_markdown_export_once(
    keeper: Keeper,
    root: str | Path,
    *,
    include_system: bool,
    include_parts: bool = False,
    include_versions: bool = False,
    allow_existing: bool = False,
    mirror_entry: MarkdownMirrorEntry | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one markdown export pass, optionally preserving an existing mirror."""
    from .markdown_export import _write_markdown_export

    out_dir = _resolve_root(root)
    if out_dir.exists():
        if not out_dir.is_dir():
            raise ValueError(f"{out_dir} exists and is not a directory")
        if not allow_existing and any(out_dir.iterdir()):
            raise ValueError(f"output directory {out_dir} is not empty")
    else:
        out_dir.mkdir(parents=True)

    prev_map = _load_map(out_dir) if allow_existing else {}

    with tempfile.TemporaryDirectory(prefix="keep-md-export-", dir=out_dir.parent) as tmp:
        tmpdir = Path(tmp)
        count, info = _write_markdown_export(
            keeper,
            tmpdir,
            include_system=include_system,
            include_parts=include_parts,
            include_versions=include_versions,
            progress=None,
        )

        new_map: dict[str, str] = {}
        new_paths: set[Path] = set()
        for file_path in sorted(tmpdir.rglob("*.md")):
            rel_path = file_path.relative_to(tmpdir)
            text = file_path.read_text(encoding="utf-8")
            keep_id = _keep_id_from_exported_file(rel_path, text)
            if keep_id:
                new_map[rel_path.with_suffix("").as_posix()] = keep_id
            new_paths.add(rel_path)
            dest = out_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest)

    if allow_existing:
        old_paths = {Path(f"{export_ref}.md") for export_ref in prev_map}
        stale_paths = sorted(old_paths - new_paths, reverse=True)
        for rel_path in stale_paths:
            target = out_dir / rel_path
            if target.exists():
                target.unlink()
                _prune_empty_dirs(out_dir, target.parent)

    _write_map(out_dir, new_map)
    _write_state(out_dir, entry=mirror_entry, count=count, info=info)
    return count, info


def _drain_sync_outbox(keeper: Keeper, *, discard: bool) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    discarded = 0
    while True:
        rows = keeper._document_store.dequeue_sync_outbox(limit=200)
        if not rows:
            break
        outbox_ids = [row["outbox_id"] for row in rows]
        if discard:
            keeper._document_store.complete_sync_outbox(outbox_ids)
            discarded += len(rows)
            continue
        events.extend(rows)
        keeper._document_store.complete_sync_outbox(outbox_ids)
    return events, discarded


def clear_sync_outbox(keeper: Keeper) -> int:
    """Discard all pending sync-outbox rows after a baseline full export.

    This is for one-shot baseline exports that have already materialized the
    entire store state into a mirror root, such as the initial `--sync`
    registration path. It should not run after ordinary mirror polling passes,
    because that would drop events that arrived while another mirror was
    exporting.
    """
    _events, discarded = _drain_sync_outbox(keeper, discard=True)
    return discarded


def poll_markdown_mirrors(keeper: Keeper) -> dict[str, int]:
    # v1 uses the sync outbox only as a precise trigger boundary. Any activity
    # is coalesced by the mirror interval. Structural changes still force a
    # whole-mirror replan; ordinary note/part/version/edge changes can now be
    # handled as bounded incremental rewrites through the shared dependency
    # service.
    entries = load_markdown_mirrors(keeper)
    from .types import utc_now as _utc_now

    if not entries:
        _events, discarded = _drain_sync_outbox(keeper, discard=True)
        return {"checked": 0, "exported": 0, "errors": 0, "discarded": discarded}

    now = datetime.now(timezone.utc)
    now_ts = _utc_now()
    dirty = False
    stats = {"checked": 0, "exported": 0, "errors": 0, "discarded": 0}
    events, _discarded = _drain_sync_outbox(keeper, discard=False)
    if events:
        for entry in entries:
            if not entry.enabled:
                continue
            plan = _plan_markdown_mirror_update(keeper, entry, events)
            if not plan.full_replan and not plan.note_ids:
                continue
            event_times = [
                (row.get("created_at") or now_ts)
                for row in events
                if row.get("collection") == _DOC_COLLECTION
            ]
            pending_since = min(
                [entry.pending_since] + event_times
                if entry.pending_since else event_times
            )
            if entry.pending_since != pending_since:
                entry.pending_since = pending_since
            if plan.full_replan:
                entry.pending_full_replan = True
                entry.pending_note_ids = []
            elif not entry.pending_full_replan:
                merged = set(entry.pending_note_ids)
                merged.update(plan.note_ids)
                entry.pending_note_ids = sorted(merged)
            dirty = True
    for entry in entries:
        if entry.pending_since and not entry.pending_full_replan and not entry.pending_note_ids:
            entry.pending_since = ""
            dirty = True
    for entry in entries:
        if not entry.is_due(now):
            continue
        plan = MarkdownMirrorUpdatePlan(
            full_replan=entry.pending_full_replan or not entry.pending_note_ids,
            note_ids=tuple(entry.pending_note_ids),
        )
        if not plan.full_replan and not plan.note_ids:
            entry.pending_since = ""
            entry.last_error = ""
            entry.pending_note_ids = []
            entry.pending_full_replan = False
            dirty = True
            continue
        stats["checked"] += 1
        try:
            if plan.full_replan:
                run_markdown_export_once(
                    keeper,
                    entry.root,
                    include_system=entry.include_system,
                    include_parts=entry.include_parts,
                    include_versions=entry.include_versions,
                    allow_existing=True,
                    mirror_entry=entry,
                )
            else:
                run_markdown_export_incremental(
                    keeper,
                    entry.root,
                    note_ids=list(plan.note_ids),
                    include_system=entry.include_system,
                    include_parts=entry.include_parts,
                    include_versions=entry.include_versions,
                    mirror_entry=entry,
                )
            entry.last_run = now_ts
            entry.pending_since = ""
            entry.pending_note_ids = []
            entry.pending_full_replan = False
            entry.last_error = ""
            stats["exported"] += 1
        except Exception as exc:
            entry.last_error = str(exc)
            logger.warning("Markdown mirror export failed for %s: %s", entry.root, exc)
            stats["errors"] += 1
        dirty = True

    if dirty:
        save_markdown_mirrors(keeper, entries)
    return stats
