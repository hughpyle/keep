"""
Tests for KeepNotesMiddleware.

Uses mock providers — no ML models or network.
"""

import pytest

from keep.api import Keeper

pytest.importorskip("langchain_core")

from langchain_core.messages import HumanMessage, SystemMessage
from keep.langchain.middleware import KeepNotesMiddleware


@pytest.fixture
def keeper(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    kp._get_embedding_provider()
    return kp


@pytest.fixture
def middleware(keeper):
    # search_limit=30 to handle mock store's insertion-order iteration
    return KeepNotesMiddleware(keeper=keeper, search_limit=30)


@pytest.fixture
def scoped_middleware(keeper):
    return KeepNotesMiddleware(keeper=keeper, user_id="alice", search_limit=30)


class TestMiddlewareInject:

    def test_inject_adds_system_message(self, middleware, keeper):
        keeper.set_now("Working on tests")
        keeper.put("Important fact", id="note:1")
        messages = [HumanMessage(content="What am I working on?")]
        result = middleware.inject(messages)
        # Should have a system message prepended
        assert len(result) > len(messages)
        assert isinstance(result[0], SystemMessage)
        assert "[Memory Context]" in result[0].content

    def test_inject_preserves_original_messages(self, middleware, keeper):
        keeper.set_now("Context")
        messages = [HumanMessage(content="Hello")]
        result = middleware.inject(messages)
        # Original message should be preserved (last element)
        assert result[-1].content == "Hello"

    def test_inject_includes_now(self, middleware, keeper):
        keeper.set_now("Currently debugging auth flow")
        messages = [HumanMessage(content="What should I focus on?")]
        result = middleware.inject(messages)
        assert "debugging auth" in result[0].content.lower()

    def test_inject_includes_search_results(self, middleware, keeper):
        keeper.set_now("Active context")  # replace first-time doc
        keeper.put("User prefers dark mode", id="pref:1")
        messages = [HumanMessage(content="dark mode preferences")]
        result = middleware.inject(messages)
        # System message should contain the search result
        combined = " ".join(m.content for m in result if isinstance(m, SystemMessage))
        assert "dark mode" in combined.lower()

    def test_inject_empty_messages(self, middleware):
        """Empty message list still gets now context injected."""
        result = middleware.inject([])
        # Now doc has first-time content, so context IS injected
        assert isinstance(result, list)
        # No human message → no search query → only now context
        assert len(result) >= 1

    def test_inject_no_search_without_human_message(self, middleware):
        """Without a human message, only now context is injected."""
        messages = [SystemMessage(content="You are helpful")]
        result = middleware.inject(messages)
        assert isinstance(result, list)
        # Should have now context + original system message
        assert len(result) >= 1


class TestMiddlewareFailOpen:

    def test_fail_open_returns_original(self, keeper):
        """With fail_open=True, errors return original messages."""
        mw = KeepNotesMiddleware(keeper=keeper, fail_open=True)
        # Force an error by breaking the keeper
        mw._keeper = None  # type: ignore
        messages = [HumanMessage(content="test")]
        result = mw.inject(messages)
        assert len(result) == 1
        assert result[0].content == "test"

    def test_fail_closed_raises(self, keeper):
        """With fail_open=False, errors propagate."""
        mw = KeepNotesMiddleware(keeper=keeper, fail_open=False)
        mw._keeper = None  # type: ignore
        messages = [HumanMessage(content="test")]
        with pytest.raises(Exception):
            mw.inject(messages)


class TestMiddlewareRunnable:

    def test_as_runnable(self, middleware, keeper):
        keeper.set_now("Active context")
        runnable = middleware.as_runnable()
        messages = [HumanMessage(content="What's happening?")]
        result = runnable.invoke(messages)
        assert isinstance(result, list)
        assert len(result) > 0


class TestMiddlewareScoped:

    def test_scoped_includes_user_context(self, scoped_middleware, keeper):
        keeper.set_now("Alice is debugging", scope="alice")
        messages = [HumanMessage(content="What am I doing?")]
        result = scoped_middleware.inject(messages)
        assert len(result) > 1
        assert "alice" in result[0].content.lower() or "debugging" in result[0].content.lower()
