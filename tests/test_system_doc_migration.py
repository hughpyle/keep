"""Tests for system doc migration behavior."""

from keep.api import Keeper
from keep.system_docs import (
    SYSTEM_DOC_DIR,
    _bundled_doc_hash,
    _content_hash,
    _load_frontmatter,
    migrate_system_documents,
    system_doc_migration_needed,
)


def _fail(*_args, **_kwargs):
    raise AssertionError("public Keeper method should not be used during migration")


def test_migrate_file_uri_system_doc_uses_direct_store_path(
    mock_providers,
    tmp_path,
    monkeypatch,
) -> None:
    """Legacy file:// system docs migrate without public flow wrappers."""
    kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
    doc_coll = kp._resolve_doc_collection()
    old_id = "file:///tmp/state-get.md"
    bundled_content, bundled_tags = _load_frontmatter(SYSTEM_DOC_DIR / "state-get.md")
    kp._document_store.upsert(
        doc_coll,
        old_id,
        bundled_content,
        {**bundled_tags, "category": "system", "bundled_hash": _content_hash(bundled_content)},
        content_hash=_content_hash(bundled_content),
    )
    kp._config.system_docs_hash = ""

    monkeypatch.setattr(kp, "list_items", _fail)
    monkeypatch.setattr(kp, "put", _fail)
    monkeypatch.setattr(kp, "delete", _fail)
    monkeypatch.setattr(kp, "exists", _fail)
    monkeypatch.setattr(kp, "get", _fail)

    stats = migrate_system_documents(kp)

    migrated = kp._document_store.get(doc_coll, ".state/get")
    assert migrated is not None
    assert migrated.summary == bundled_content
    assert kp._document_store.get(doc_coll, old_id) is None
    assert stats["migrated"] >= 1


def test_migrate_old_prefix_system_doc_uses_direct_store_path(
    mock_providers,
    tmp_path,
    monkeypatch,
) -> None:
    """Legacy _system:* IDs migrate without public flow wrappers."""
    kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
    doc_coll = kp._resolve_doc_collection()
    old_id = "_system:now"
    bundled_content, bundled_tags = _load_frontmatter(SYSTEM_DOC_DIR / "now.md")
    kp._document_store.upsert(
        doc_coll,
        old_id,
        bundled_content,
        {**bundled_tags, "category": "system", "bundled_hash": _content_hash(bundled_content)},
        content_hash=_content_hash(bundled_content),
    )
    kp._config.system_docs_hash = ""

    monkeypatch.setattr(kp, "list_items", _fail)
    monkeypatch.setattr(kp, "put", _fail)
    monkeypatch.setattr(kp, "delete", _fail)
    monkeypatch.setattr(kp, "exists", _fail)
    monkeypatch.setattr(kp, "get", _fail)

    stats = migrate_system_documents(kp)

    migrated = kp._document_store.get(doc_coll, ".now")
    assert migrated is not None
    assert migrated.summary == bundled_content
    assert kp._document_store.get(doc_coll, old_id) is None
    assert stats["migrated"] >= 1


def test_migrate_repairs_missing_bundled_prompt_state_tag(
    mock_providers,
    tmp_path,
) -> None:
    """Bundled-tag drift is repaired even when body content already matches."""
    kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
    doc_coll = kp._resolve_doc_collection()
    bundled_content, bundled_tags = _load_frontmatter(
        SYSTEM_DOC_DIR / "prompt-agent-reflect.md"
    )
    # Simulate an older/broken store entry: bundled body matches, but the
    # bundled state tag is missing and bundled_hash only tracks body content.
    kp._document_store.upsert(
        doc_coll,
        ".prompt/agent/reflect",
        bundled_content,
        {
            "category": "system",
            "context": "prompt",
            "bundled_hash": _content_hash(bundled_content),
        },
        content_hash=_content_hash(bundled_content),
        archive=False,
    )
    kp._config.system_docs_hash = ""

    stats = migrate_system_documents(kp)

    repaired = kp._document_store.get(doc_coll, ".prompt/agent/reflect")
    assert repaired is not None
    assert repaired.tags["state"] == bundled_tags["state"]
    assert repaired.tags["bundled_hash"] == _content_hash(bundled_content)
    assert repaired.tags["bundled_doc_hash"] == _bundled_doc_hash(
        bundled_content,
        {**bundled_tags, "category": "system"},
    )
    assert stats["migrated"] >= 1


def test_system_doc_migration_needed_when_prompt_tags_drift_but_hash_gate_matches(
    mock_providers,
    tmp_path,
) -> None:
    """Startup should rerun system-doc migration for bundled-tag drift."""
    kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
    doc_coll = kp._resolve_doc_collection()
    bundled_content, _ = _load_frontmatter(SYSTEM_DOC_DIR / "prompt-agent-reflect.md")
    kp._document_store.upsert(
        doc_coll,
        ".prompt/agent/reflect",
        bundled_content,
        {
            "category": "system",
            "context": "prompt",
            "bundled_hash": _content_hash(bundled_content),
        },
        content_hash=_content_hash(bundled_content),
        archive=False,
    )
    from keep.system_docs import _bundled_docs_hash
    kp._config.system_docs_hash = _bundled_docs_hash()

    assert system_doc_migration_needed(kp) is True
