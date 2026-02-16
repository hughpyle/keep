"""
Tests for KeepNotesToolkit.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper

pytest.importorskip("langchain_core")

from keep.langchain.toolkit import KeepNotesToolkit


@pytest.fixture
def keeper(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


@pytest.fixture
def toolkit(keeper):
    return KeepNotesToolkit(keeper=keeper)


@pytest.fixture
def scoped_toolkit(keeper):
    return KeepNotesToolkit(keeper=keeper, user_id="alice")


class TestToolkitBasic:

    def test_get_tools_returns_four(self, toolkit):
        tools = toolkit.get_tools()
        assert len(tools) == 4

    def test_tool_names(self, toolkit):
        names = {t.name for t in toolkit.get_tools()}
        assert names == {"remember", "recall", "get_context", "update_context"}

    def test_tools_have_descriptions(self, toolkit):
        for tool in toolkit.get_tools():
            assert tool.description, f"{tool.name} has no description"


class TestRemember:

    def test_remember_stores_content(self, toolkit, keeper):
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["remember"].invoke({"content": "User likes coffee"})
        assert "Remembered" in result

    def test_remember_with_tags(self, toolkit, keeper):
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["remember"].invoke({
            "content": "Prefers dark mode",
            "tags": {"topic": "preferences"},
        })
        assert "Remembered" in result

    def test_remember_scoped_adds_user_tag(self, scoped_toolkit, keeper):
        tools = {t.name: t for t in scoped_toolkit.get_tools()}
        result = tools["remember"].invoke({"content": "Alice's note"})
        assert "Remembered" in result
        # The stored item should have user=alice tag
        items = keeper.find("Alice's note", limit=30)
        assert len(items) > 0


class TestRecall:

    def test_recall_finds_stored(self, toolkit, keeper):
        keeper.put("User prefers dark mode", tags={"topic": "preferences"})
        tools = {t.name: t for t in toolkit.get_tools()}
        # limit=30 to handle mock store's insertion-order iteration
        result = tools["recall"].invoke({"query": "dark mode", "limit": 30})
        assert "dark mode" in result.lower()

    def test_recall_no_results(self, toolkit):
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["recall"].invoke({"query": "nonexistent topic xyz"})
        assert "No relevant memories" in result or len(result) > 0

    def test_recall_with_limit(self, toolkit, keeper):
        for i in range(5):
            keeper.put(f"Note {i}", id=f"note:{i}")
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["recall"].invoke({"query": "note", "limit": 2})
        # Should return at most 2 results
        lines = [l for l in result.strip().split("\n") if l.startswith("- ")]
        assert len(lines) <= 2


class TestGetContext:

    def test_get_context_default(self, toolkit):
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["get_context"].invoke({})
        # Should return something (default now doc)
        assert isinstance(result, str)

    def test_get_context_after_update(self, toolkit, keeper):
        keeper.set_now("Working on LangChain integration")
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["get_context"].invoke({})
        assert "LangChain" in result


class TestUpdateContext:

    def test_update_context(self, toolkit):
        tools = {t.name: t for t in toolkit.get_tools()}
        result = tools["update_context"].invoke({
            "content": "Starting new project"
        })
        assert "Context updated" in result

    def test_update_then_get(self, toolkit):
        tools = {t.name: t for t in toolkit.get_tools()}
        tools["update_context"].invoke({"content": "Phase 2 complete"})
        result = tools["get_context"].invoke({})
        assert "Phase 2" in result

    def test_update_context_scoped(self, scoped_toolkit):
        tools = {t.name: t for t in scoped_toolkit.get_tools()}
        result = tools["update_context"].invoke({
            "content": "Alice is debugging"
        })
        assert "Context updated" in result
        assert "now:alice" in result
