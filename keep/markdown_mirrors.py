"""Daemon-owned markdown mirror registry and export passes."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

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
    # still results in a full mirror export pass once the debounce interval
    # elapses. True incremental export will consume the same outbox events but
    # must also resolve the reverse dependency graph for inverse-edge
    # frontmatter before it can narrow writes safely.
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
        earliest_by_root: dict[str, str] = {}
        for entry in entries:
            if not entry.enabled:
                continue
            if entry.pending_since:
                earliest_by_root[entry.root] = entry.pending_since
            for row in events:
                if row.get("collection") != _DOC_COLLECTION:
                    continue
                created_at = row.get("created_at") or now_ts
                existing = earliest_by_root.get(entry.root)
                if existing is None or created_at < existing:
                    earliest_by_root[entry.root] = created_at
        for entry in entries:
            pending_since = earliest_by_root.get(entry.root)
            if pending_since and entry.pending_since != pending_since:
                entry.pending_since = pending_since
                dirty = True
    for entry in entries:
        if not entry.is_due(now):
            continue
        stats["checked"] += 1
        try:
            run_markdown_export_once(
                keeper,
                entry.root,
                include_system=entry.include_system,
                include_parts=entry.include_parts,
                include_versions=entry.include_versions,
                allow_existing=True,
                mirror_entry=entry,
            )
            entry.last_run = now_ts
            entry.pending_since = ""
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
