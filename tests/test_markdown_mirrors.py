"""Tests for daemon-owned markdown mirror registry and export passes."""

from __future__ import annotations

import json
import time

import pytest

from keep.api import Keeper
from keep.document_store import PartInfo
from keep.markdown_mirrors import (
    MarkdownMirrorEntry,
    add_markdown_mirror,
    clear_sync_outbox,
    list_markdown_mirrors,
    next_markdown_mirror_delay,
    poll_markdown_mirrors,
    run_markdown_export_once,
    save_markdown_mirrors,
)
from keep.types import utc_now
from keep.watches import add_watch


def _create_tagdoc(kp: Keeper, key: str, inverse: str) -> None:
    doc_coll = kp._resolve_doc_collection()
    now = utc_now()
    kp._document_store.upsert(
        collection=doc_coll,
        id=f".tag/{key}",
        summary=f"Tag: {key}",
        tags={
            "_inverse": inverse,
            "_created": now,
            "_updated": now,
            "_source": "inline",
            "category": "system",
        },
        archive=False,
    )


def test_add_markdown_mirror_rejects_watch_overlap(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        watched = tmp_path / "watched"
        watched.mkdir()
        add_watch(kp, str(watched), "directory")
        with pytest.raises(ValueError, match="overlaps watched source"):
            add_markdown_mirror(kp, watched / "vault")
    finally:
        kp.close()


def test_add_watch_rejects_markdown_mirror_overlap(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        root = tmp_path / "vault"
        root.mkdir()
        add_markdown_mirror(kp, root)
        with pytest.raises(ValueError, match="overlaps markdown sync root"):
            add_watch(kp, str(root / "notes"), "directory")
    finally:
        kp.close()


def test_put_from_markdown_mirror_root_rejected(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        root = tmp_path / "vault"
        root.mkdir()
        add_markdown_mirror(kp, root)
        note_path = root / "note.md"
        note_path.write_text("hello", encoding="utf-8")
        with pytest.raises(ValueError, match="synced markdown mirror root"):
            kp.put(uri=f"file://{note_path}")
    finally:
        kp.close()


def test_run_markdown_export_once_writes_map_state_and_cleans_stale(
    mock_providers, tmp_path,
):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        kp.put("Alpha body", id="alpha")
        root = tmp_path / "vault"
        root.mkdir()
        obsidian = root / ".obsidian"
        obsidian.mkdir()
        (obsidian / "workspace.json").write_text("{}", encoding="utf-8")

        count, info = run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )
        assert count == 1
        assert info["document_count"] == 1
        assert (root / "alpha.md").is_file()
        assert (root / ".keep-sync" / "map.tsv").is_file()
        assert (root / ".keep-sync" / "state.json").is_file()
        assert (root / ".obsidian" / "workspace.json").is_file()

        kp.delete("alpha")
        kp.put("Beta body", id="beta")
        run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )

        assert not (root / "alpha.md").exists()
        assert (root / "beta.md").is_file()
        state = json.loads((root / ".keep-sync" / "state.json").read_text(encoding="utf-8"))
        assert state["count"] == 1
        map_text = (root / ".keep-sync" / "map.tsv").read_text(encoding="utf-8")
        assert "beta\tbeta" in map_text
        assert "alpha\talpha" not in map_text
    finally:
        kp.close()


def test_run_markdown_export_once_maps_read_only_sidecars(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        kp.put("Version one " + ("a" * 700), id="doc1")
        kp.put("Version two " + ("b" * 700), id="doc1")
        doc_coll = kp._resolve_doc_collection()
        kp._document_store.upsert_single_part(
            doc_coll,
            "doc1",
            PartInfo(
                part_num=1,
                summary="Part one",
                tags={"_part_num": "1", "_base_id": "doc1"},
                created_at=utc_now(),
            ),
        )

        root = tmp_path / "vault"
        count, _info = run_markdown_export_once(
            kp,
            root,
            include_system=False,
            include_parts=True,
            include_versions=True,
            allow_existing=True,
        )

        assert count == 1
        entries = (root / ".keep-sync" / "map.tsv").read_text(encoding="utf-8")
        assert "doc1\tdoc1" in entries
        assert "doc1/@P{1}\tdoc1@P{1}" in entries
        assert "doc1/@V{1}\tdoc1@V{1}" in entries
    finally:
        kp.close()


def test_list_markdown_mirrors_returns_saved_entries(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        root = tmp_path / "vault"
        add_markdown_mirror(kp, root, include_parts=True, interval="PT5M")
        entries = list_markdown_mirrors(kp)
        assert len(entries) == 1
        assert entries[0].root == str(root.resolve())
        assert entries[0].include_parts is True
        assert entries[0].interval == "PT5M"
    finally:
        kp.close()


def test_poll_markdown_mirrors_debounces_sync_outbox(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        root = tmp_path / "vault"
        root.mkdir()
        run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )
        add_markdown_mirror(kp, root, interval="PT5M")

        kp.put("Alpha body", id="alpha")
        stats = poll_markdown_mirrors(kp)
        assert stats["exported"] == 0
        assert not (root / "alpha.md").exists()

        entries = list_markdown_mirrors(kp)
        assert len(entries) == 1
        assert entries[0].pending_since
        entries[0].pending_since = "2000-01-01T00:00:00"
        save_markdown_mirrors(kp, entries)

        stats = poll_markdown_mirrors(kp)
        assert stats["exported"] == 1
        assert (root / "alpha.md").is_file()

        refreshed = list_markdown_mirrors(kp)
        assert refreshed[0].pending_since == ""
        assert refreshed[0].last_run
    finally:
        kp.close()


def test_poll_markdown_mirrors_incremental_rewrites_only_changed_note(
    mock_providers, tmp_path,
):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        kp.put("Alpha body", id="alpha")
        kp.put("Beta body", id="beta")
        root = tmp_path / "vault"
        root.mkdir()
        run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )
        clear_sync_outbox(kp)
        add_markdown_mirror(kp, root, interval="PT1S")

        alpha_path = root / "alpha.md"
        beta_path = root / "beta.md"
        alpha_before = alpha_path.stat().st_mtime_ns
        beta_before = beta_path.stat().st_mtime_ns

        time.sleep(0.01)
        kp.put("Alpha body updated", id="alpha")
        stats = poll_markdown_mirrors(kp)
        assert stats["exported"] == 0
        entries = list_markdown_mirrors(kp)
        entries[0].pending_since = "2000-01-01T00:00:00"
        save_markdown_mirrors(kp, entries)
        stats = poll_markdown_mirrors(kp)

        assert stats["exported"] == 1
        assert alpha_path.stat().st_mtime_ns > alpha_before
        assert beta_path.stat().st_mtime_ns == beta_before
    finally:
        kp.close()


def test_poll_markdown_mirrors_incremental_rewrites_inverse_target_on_source_display_change(
    mock_providers, tmp_path,
):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        _create_tagdoc(kp, "speaker", "said")
        kp.put("Joanna note", id="Joanna")
        kp.put("Session body", id="session-1", tags={"speaker": "Joanna"})
        kp.put("Unrelated body", id="other")

        root = tmp_path / "vault"
        root.mkdir()
        run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )
        clear_sync_outbox(kp)
        add_markdown_mirror(kp, root, interval="PT1S")

        source_path = root / "session-1.md"
        target_path = root / "Joanna.md"
        other_path = root / "other.md"
        source_before = source_path.stat().st_mtime_ns
        target_before = target_path.stat().st_mtime_ns
        other_before = other_path.stat().st_mtime_ns

        time.sleep(0.01)
        kp.put("Session body renamed", id="session-1", summary="Session renamed", tags={"speaker": "Joanna"})
        stats = poll_markdown_mirrors(kp)
        assert stats["exported"] == 0
        entries = list_markdown_mirrors(kp)
        entries[0].pending_since = "2000-01-01T00:00:00"
        save_markdown_mirrors(kp, entries)
        stats = poll_markdown_mirrors(kp)

        assert stats["exported"] == 1
        assert source_path.stat().st_mtime_ns > source_before
        assert target_path.stat().st_mtime_ns > target_before
        assert other_path.stat().st_mtime_ns == other_before
    finally:
        kp.close()


def test_poll_markdown_mirrors_incremental_rewrites_old_and_new_edge_targets(
    mock_providers, tmp_path,
):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        _create_tagdoc(kp, "speaker", "said")
        kp.put("Joanna note", id="Joanna")
        kp.put("Nate note", id="Nate")
        kp.put("Session body", id="session-2", tags={"speaker": "Joanna"})
        kp.put("Unrelated body", id="other")

        root = tmp_path / "vault"
        root.mkdir()
        run_markdown_export_once(
            kp,
            root,
            include_system=False,
            allow_existing=True,
        )
        clear_sync_outbox(kp)
        add_markdown_mirror(kp, root, interval="PT1S")

        source_path = root / "session-2.md"
        joanna_path = root / "Joanna.md"
        nate_path = root / "Nate.md"
        other_path = root / "other.md"
        source_before = source_path.stat().st_mtime_ns
        joanna_before = joanna_path.stat().st_mtime_ns
        nate_before = nate_path.stat().st_mtime_ns
        other_before = other_path.stat().st_mtime_ns

        time.sleep(0.01)
        kp.put("Session body", id="session-2", tags={"speaker": "Nate"})
        stats = poll_markdown_mirrors(kp)
        assert stats["exported"] == 0
        entries = list_markdown_mirrors(kp)
        entries[0].pending_since = "2000-01-01T00:00:00"
        save_markdown_mirrors(kp, entries)
        stats = poll_markdown_mirrors(kp)

        assert stats["exported"] == 1
        assert source_path.stat().st_mtime_ns > source_before
        assert joanna_path.stat().st_mtime_ns > joanna_before
        assert nate_path.stat().st_mtime_ns > nate_before
        assert other_path.stat().st_mtime_ns == other_before
    finally:
        kp.close()


def test_clear_sync_outbox_discards_pending_rows(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path / "store")
    try:
        kp.put("Alpha body", id="alpha")
        assert kp._document_store.sync_outbox_depth() > 0
        discarded = clear_sync_outbox(kp)
        assert discarded > 0
        assert kp._document_store.sync_outbox_depth() == 0
    finally:
        kp.close()


def test_next_markdown_mirror_delay_prefers_pending_due_time():
    ready = MarkdownMirrorEntry(
        root="/tmp/one",
        interval="PT30S",
        enabled=True,
        pending_since="2000-01-01T00:00:00",
    )
    idle = MarkdownMirrorEntry(
        root="/tmp/two",
        interval="PT30S",
        enabled=True,
        pending_since="",
    )
    delay = next_markdown_mirror_delay([idle, ready])
    assert delay == 0.0
