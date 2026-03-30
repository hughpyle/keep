"""Tests for archived-hash restore reuse on put()."""

import hashlib

from keep.api import Keeper
from keep.document_store import PartInfo
from keep.types import utc_now


def _summary_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def test_put_restores_archived_summary_tags_but_keeps_current_analysis(
    mock_providers, tmp_path
):
    kp = Keeper(store_path=tmp_path)
    doc_coll = kp._resolve_doc_collection()

    # Initial version A with explicit derived state.
    kp.put("alpha content", id="doc:1", summary="summary alpha", tags={"topic": "alpha"})
    doc_a = kp._document_store.get(doc_coll, "doc:1")
    assert doc_a is not None
    hash_a = doc_a.content_hash
    tags_a = dict(doc_a.tags)
    tags_a["kind"] = "alpha"
    tags_a["_tagged_hash"] = hash_a
    tags_a["_tagged_summary_hash"] = _summary_hash("summary alpha")
    kp._document_store.update_tags(doc_coll, "doc:1", tags_a)

    # Version B replaces A and becomes the currently analyzed head.
    kp.put("beta content", id="doc:1", summary="summary beta", tags={"topic": "beta"})
    doc_b = kp._document_store.get(doc_coll, "doc:1")
    assert doc_b is not None
    hash_b = doc_b.content_hash
    tags_b = dict(doc_b.tags)
    tags_b["_analyzed_hash"] = hash_b
    tags_b["_analyzed_version"] = "1"
    kp._document_store.update_tags(doc_coll, "doc:1", tags_b)
    parts_b = [
        PartInfo(
            part_num=1,
            summary="Beta section",
            tags={"topic": "beta-part"},
            content="Beta body",
            created_at=utc_now(),
        )
    ]
    kp._document_store.upsert_parts(doc_coll, "doc:1", parts_b)

    # Returning to A restores archived head data but leaves current analysis intact.
    restored = kp.put("alpha content", id="doc:1")
    current = kp.get("doc:1")
    assert restored.summary == "summary alpha"
    assert current is not None
    assert current.summary == "summary alpha"
    assert current.tags["topic"] == "alpha"
    assert current.tags["kind"] == "alpha"
    assert current.tags["_restored_hash"] == hash_a
    assert current.tags["_restored_from_version"] == "1"
    assert current.tags["_analyzed_hash"] == hash_b
    assert current.tags["_analyzed_version"] == "1"

    parts = kp.list_parts("doc:1")
    assert [p.summary for p in parts] == ["Beta section"]
    assert kp._document_store.version_count(doc_coll, "doc:1") == 2
