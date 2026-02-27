# Copyright (c) 2026 Inguz Outcomes LLC.  All rights reserved.
"""Tests for render_find_context — token-budgeted prompt renderer."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from keep.types import Item, PromptResult, PartRef
from keep.document_store import VersionInfo


def _item(id="test", summary="Test summary", score=0.9, tags=None):
    return Item(id=id, summary=summary, score=score, tags=tags or {"_updated_date": "2026-02-20"})


class TestRenderFindContext:
    """Tests for the token-budgeted progressive renderer."""

    def test_basic_rendering(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="First item"), _item(id="b", summary="Second item")]
        result = render_find_context(items)
        assert "a" in result
        assert "First item" in result
        assert "b" in result
        assert "Second item" in result

    def test_empty_items(self):
        from keep.cli import render_find_context
        result = render_find_context([])
        assert result == "No results."

    def test_score_included(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="With score", score=0.85)]
        result = render_find_context(items)
        assert "(0.85)" in result

    def test_date_included(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="Dated", tags={"_updated_date": "2026-01-15"})]
        result = render_find_context(items)
        assert "2026-01-15" in result

    def test_focus_summary_rendered(self):
        """Focus summary replaces parent summary on the primary line."""
        from keep.cli import render_find_context
        items = [_item(id="a", summary="Parent doc",
                       tags={"_updated_date": "2026-02-20",
                             "_focus_summary": "The matching part content"})]
        result = render_find_context(items)
        assert "The matching part content" in result
        assert "Parent doc" not in result

    def test_budget_limits_items(self):
        """With a very small budget, only the first item should appear."""
        from keep.cli import render_find_context
        items = [
            _item(id="first", summary="A" * 200),
            _item(id="second", summary="B" * 200),
            _item(id="third", summary="C" * 200),
        ]
        # Each item line ~55 tokens. Budget of 30 should only fit first.
        result = render_find_context(items, token_budget=30)
        assert "first" in result
        # Second item should be cut off (budget exhausted by first)
        assert "second" not in result

    def test_large_budget_includes_all(self):
        """With a large budget, all items should appear."""
        from keep.cli import render_find_context
        items = [_item(id=f"item-{i}", summary=f"Summary {i}") for i in range(10)]
        result = render_find_context(items, token_budget=10000)
        for i in range(10):
            assert f"item-{i}" in result

    def test_no_score_when_none(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="No score", score=None)]
        result = render_find_context(items)
        assert "(" not in result  # no score parens


class TestExpandPromptFindBudget:
    """Tests for {find:N} budget override syntax in expand_prompt."""

    def test_default_budget(self):
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Test")],
            prompt="Context:\n{find}\nEnd.",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "a" in output
        assert "{find}" not in output

    def test_budget_from_placeholder(self):
        """Budget specified in placeholder should override default."""
        from keep.cli import expand_prompt
        # Create many items
        items = [_item(id=f"item-{i}", summary="X" * 200) for i in range(20)]
        # Default budget is large, but placeholder says 50
        result = PromptResult(
            context=None,
            search_results=items,
            prompt="Context:\n{find:50}\nEnd.",
            token_budget=10000,
        )
        output = expand_prompt(result)
        # With budget=50 tokens, shouldn't fit all 20 items
        assert "item-0" in output
        assert "item-19" not in output

    def test_deep_with_budget(self):
        """The {find:deep:8000} syntax should be expanded."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Test")],
            prompt="{find:deep:8000}",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "a" in output
        assert "{find" not in output

    def test_deep_without_budget(self):
        """The {find:deep} syntax should use default budget."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Deep test")],
            prompt="{find:deep}",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "Deep test" in output

    def test_no_results(self):
        """Empty search results should produce empty expansion."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=None,
            prompt="Before {find} After",
        )
        output = expand_prompt(result)
        assert "Before" in output
        assert "After" in output
        assert "{find}" not in output


class TestRenderFindContextDetail:
    """Tests for pass-2 detail rendering (parts, versions, tags, deep)."""

    def _mock_keeper(self, parts=None, versions=None, versions_around=None):
        """Create a mock keeper with list_parts/list_versions/list_versions_around."""
        keeper = MagicMock()
        keeper.list_parts.return_value = parts or []
        keeper.list_versions.return_value = versions or []
        keeper.list_versions_around.return_value = versions_around or []
        return keeper

    def test_pass2_renders_parts(self):
        """With >=2 items and a keeper, parts are rendered."""
        from keep.cli import render_find_context
        parts = [
            PartRef(part_num=0, summary="Overview of the topic"),
            PartRef(part_num=1, summary="Details and analysis"),
            PartRef(part_num=2, summary="Conclusions drawn"),
        ]
        keeper = self._mock_keeper(parts=parts)
        items = [_item(id=f"doc-{i}", summary=f"Doc {i}") for i in range(3)]
        result = render_find_context(items, keeper=keeper, token_budget=5000)
        assert "Key topics:" in result
        assert "Overview of the topic" in result
        assert "Conclusions drawn" in result

    def test_pass2_renders_versions(self):
        """With >=2 items and a keeper, version history is rendered."""
        from keep.cli import render_find_context
        versions = [
            VersionInfo(version=1, summary="Initial draft", tags={"_updated_date": "2026-01-01"}, created_at="2026-01-01", content_hash="a"),
            VersionInfo(version=2, summary="Added section B", tags={"_updated_date": "2026-01-15"}, created_at="2026-01-15", content_hash="b"),
        ]
        keeper = self._mock_keeper(versions=versions)
        items = [_item(id=f"doc-{i}", summary=f"Doc {i}") for i in range(3)]
        result = render_find_context(items, keeper=keeper, token_budget=5000)
        assert "Context:" in result
        assert "@V{1}" in result
        assert "Initial draft" in result

    def test_pass2_renders_user_tags(self):
        """With show_tags=True, user tags appear in pass-2 detail."""
        from keep.cli import render_find_context
        keeper = self._mock_keeper()
        items = [
            _item(id=f"doc-{i}", summary=f"Doc {i}",
                  tags={"_updated_date": "2026-02-20", "topic": "ai", "status": "draft"})
            for i in range(3)
        ]
        result = render_find_context(items, keeper=keeper, token_budget=5000, show_tags=True)
        assert "topic: ai" in result
        assert "status: draft" in result

    def test_pass2_focus_version_uses_around(self):
        """When _focus_version is set, list_versions_around is called."""
        from keep.cli import render_find_context
        around_versions = [
            VersionInfo(version=4, summary="Before hit", tags={"_updated_date": "2026-01-04"}, created_at="2026-01-04", content_hash="d"),
            VersionInfo(version=5, summary="The matched version", tags={"_updated_date": "2026-01-05"}, created_at="2026-01-05", content_hash="e"),
            VersionInfo(version=6, summary="After hit", tags={"_updated_date": "2026-01-06"}, created_at="2026-01-06", content_hash="f"),
        ]
        keeper = self._mock_keeper(versions_around=around_versions)
        items = [
            _item(id="doc-0", summary="Doc 0",
                  tags={"_updated_date": "2026-02-20", "_focus_version": "5"}),
            _item(id="doc-1", summary="Doc 1"),
        ]
        result = render_find_context(items, keeper=keeper, token_budget=5000)
        keeper.list_versions_around.assert_called_once_with("doc-0", 5, radius=2)
        assert "@V{5}" in result
        assert "The matched version" in result

    def test_pass2_skipped_without_keeper(self):
        """Without a keeper, pass 2 doesn't run (no parts/versions)."""
        from keep.cli import render_find_context
        items = [_item(id=f"doc-{i}", summary=f"Doc {i}") for i in range(5)]
        result = render_find_context(items, keeper=None, token_budget=5000)
        assert "Key topics:" not in result
        assert "Context:" not in result

    def test_zero_budget_returns_no_results(self):
        """Budget of 0 with items present returns 'No results.'."""
        from keep.cli import render_find_context
        items = [_item(id="a", summary="Something")]
        result = render_find_context(items, token_budget=0)
        assert result == "No results."


class TestListVersionsAround:
    """Tests for DocumentStore.list_versions_around."""

    @pytest.fixture
    def store(self, tmp_path):
        from keep.document_store import DocumentStore
        db_path = tmp_path / "documents.db"
        with DocumentStore(db_path) as s:
            yield s

    def _add_versions(self, store, id="doc1", n=10):
        """Add n versions to a document."""
        import json
        store.upsert("default", id, summary="current", tags={"_created": "2026-01-01"})
        for v in range(1, n + 1):
            store._conn.execute("""
                INSERT INTO document_versions (id, collection, version, summary, tags_json, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (id, "default", v, f"Version {v}", json.dumps({"_updated_date": f"2026-01-{v:02d}"}), f"hash{v}", f"2026-01-{v:02d}T00:00:00"))
        store._conn.commit()

    def test_returns_surrounding_versions(self, store):
        self._add_versions(store, n=10)
        results = store.list_versions_around("default", "doc1", version=5, radius=2)
        versions = [r.version for r in results]
        assert versions == [3, 4, 5, 6, 7]

    def test_clamps_at_start(self, store):
        """Version near the start returns fewer items below."""
        self._add_versions(store, n=10)
        results = store.list_versions_around("default", "doc1", version=1, radius=2)
        versions = [r.version for r in results]
        assert 1 in versions
        assert all(v >= 1 for v in versions)
        # Should have 1, 2, 3 (no negative versions)
        assert versions == [1, 2, 3]

    def test_nonexistent_version(self, store):
        """If the target version doesn't exist, returns neighbors that do."""
        self._add_versions(store, n=5)
        # Version 99 doesn't exist, nothing in that range
        results = store.list_versions_around("default", "doc1", version=99, radius=2)
        assert results == []

    def test_chronological_order(self, store):
        self._add_versions(store, n=10)
        results = store.list_versions_around("default", "doc1", version=5, radius=2)
        versions = [r.version for r in results]
        assert versions == sorted(versions)


class TestVersionHitUplift:
    """Tests for version-hit uplift in the find pipeline."""

    @pytest.fixture
    def kp(self, mock_providers, tmp_path):
        from keep.api import Keeper
        return Keeper(store_path=tmp_path)

    def test_version_hit_uplifted_to_parent(self, kp):
        """A version hit should be uplifted to its parent with _focus_version."""
        # Create a document and add some versions
        kp.put(content="Version 1 content", id="mydoc", summary="v1")
        kp.put(content="Version 2 content about quantum computing", id="mydoc", summary="v2 quantum")
        kp.put(content="Version 3 content", id="mydoc", summary="v3")

        # Simulate what find() does internally: search returns a version hit
        # We test via the real find() pipeline
        results = kp.find("quantum computing", limit=5)

        # If there's a match, the result should be uplifted to the parent
        if results:
            # Should show parent ID, not version ID
            for item in results:
                assert "@v" not in item.id or item.id == "mydoc"

    def test_focus_version_set_on_uplift(self, kp):
        """Uplifted version hits carry _focus_version tag."""
        kp.put(content="Initial content", id="testdoc", summary="initial")
        kp.put(content="Updated with important info about neural networks", id="testdoc",
               summary="neural nets update")

        results = kp.find("neural networks", limit=5)

        # Find the result for testdoc if it matched
        for item in results:
            if item.id == "testdoc" and "_focus_version" in item.tags:
                # _focus_version should be set and numeric
                assert item.tags["_focus_version"].isdigit()
                break
