"""Tests for the shared note dependency service."""

import pytest
from unittest.mock import MagicMock, patch

from keep.api import Keeper
from keep.dependencies import NoteDependencyService
from keep.document_store import PartInfo
from keep.types import utc_now
from tests.conftest import (
    MockDocumentProvider,
    MockEmbeddingProvider,
    MockSummarizationProvider,
)


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


class TestNoteDependencyService:
    @pytest.fixture
    def keeper(self, tmp_path):
        mock_reg = MagicMock()
        mock_reg.create_embedding.return_value = MockEmbeddingProvider()
        mock_reg.create_summarization.return_value = MockSummarizationProvider()
        mock_reg.create_document.return_value = MockDocumentProvider()

        with patch("keep.api.get_registry", return_value=mock_reg), \
             patch("keep._provider_lifecycle.get_registry", return_value=mock_reg), \
             patch("keep.api.CachingEmbeddingProvider", side_effect=lambda p, **kw: p), \
             patch("keep._provider_lifecycle.CachingEmbeddingProvider", side_effect=lambda p, **kw: p), \
             patch.object(Keeper, "_spawn_processor", return_value=False):
            kp = Keeper(store_path=tmp_path / "deps-test-store")
            kp._get_embedding_provider()
            yield kp

    def test_resolves_current_and_archived_edge_dependencies(self, keeper):
        _create_tagdoc(keeper, "speaker", "said")

        keeper.put(
            content="Joanna talked about hiking",
            id="session-1",
            summary="Joanna talked about hiking",
            tags={"speaker": "Joanna"},
        )
        keeper.put(
            content="Nate wrapped up the meeting",
            id="session-1",
            summary="Nate wrapped up the meeting",
            tags={"speaker": "Nate"},
        )

        doc_coll = keeper._resolve_doc_collection()
        deps = NoteDependencyService(keeper._document_store, doc_coll)

        current_targets = deps.current_targets("session-1")
        assert [(dep.relationship, dep.note_id) for dep in current_targets] == [
            ("speaker", "Nate"),
        ]

        current_sources = deps.current_sources("Nate")
        assert [(dep.relationship, dep.note_id) for dep in current_sources] == [
            ("said", "session-1"),
        ]

        archived_targets = deps.archived_targets("session-1")
        assert any(
            dep.version == 1
            and dep.relationship == "speaker"
            and dep.note_id == "Joanna"
            for dep in archived_targets
        )

        archived_sources = deps.archived_sources("Joanna")
        assert any(
            dep.relationship == "said" and dep.note_id == "session-1"
            for dep in archived_sources
        )

        assert deps.all_target_ids("session-1") == ["Nate", "Joanna"]
        assert deps.all_source_ids("Joanna") == ["session-1"]

    def test_reports_structural_surfaces(self, keeper):
        keeper.put(
            content="Current note body",
            id="doc-1",
            summary="Current note body",
        )
        keeper.put(
            content="Updated note body",
            id="doc-1",
            summary="Updated note body",
        )

        doc_coll = keeper._resolve_doc_collection()
        keeper._document_store.upsert_parts(
            doc_coll,
            "doc-1",
            [
                PartInfo(
                    part_num=1,
                    summary="Part one",
                    tags={"section": "one"},
                    created_at=utc_now(),
                ),
                PartInfo(
                    part_num=2,
                    summary="Part two",
                    tags={"section": "two"},
                    created_at=utc_now(),
                ),
            ],
        )

        deps = NoteDependencyService(keeper._document_store, doc_coll)
        surfaces = deps.structural_surfaces("doc-1")
        assert surfaces.note_id == "doc-1"
        assert surfaces.part_numbers == (1, 2)
        assert surfaces.version_numbers == (1,)

    def test_store_exposes_forward_archived_version_edges(self, keeper):
        _create_tagdoc(keeper, "speaker", "said")

        keeper.put(
            content="Joanna talked about hiking",
            id="session-2",
            summary="Joanna talked about hiking",
            tags={"speaker": "Joanna"},
        )
        keeper.put(
            content="Nate wrapped up the meeting",
            id="session-2",
            summary="Nate wrapped up the meeting",
            tags={"speaker": "Nate"},
        )

        doc_coll = keeper._resolve_doc_collection()
        rows = keeper._document_store.get_forward_version_edges(
            doc_coll, "session-2",
        )
        assert any(
            version == 1 and predicate == "speaker" and target_id == "Joanna"
            for version, predicate, target_id, _created in rows
        )
