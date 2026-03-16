"""
Tests for the MCP stdio server tool functions.

Tests the tool layer in isolation by mocking Keeper — verifies parameter
mapping, return formatting, and edge cases for the three tools:
keep_flow, keep_prompt, keep_help.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keep.types import Item, PromptResult, PromptInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(id="test-id", summary="Test summary", tags=None,
               score=None, changed=None):
    """Create a test Item."""
    return Item(
        id=id,
        summary=summary,
        tags=tags or {"_updated_date": "2026-02-20"},
        score=score,
        changed=changed,
    )


@pytest.fixture
def mock_keeper():
    """Patch _get_keeper to return a mock."""
    mock = MagicMock()
    mock._config = MagicMock()
    mock._config.budget_per_flow = 5
    with patch("keep.mcp._get_keeper", return_value=mock):
        yield mock


# ---------------------------------------------------------------------------
# keep_flow
# ---------------------------------------------------------------------------

class TestKeepFlow:

    @pytest.mark.asyncio
    async def test_flow_returns_json(self, mock_keeper):
        from keep.mcp import keep_flow
        from keep.state_doc_runtime import FlowResult
        mock_keeper.run_flow_command.return_value = FlowResult(
            status="done", ticks=1, data={"id": "test-123"},
        )
        result = await keep_flow(state="put", params={"content": "hello"})
        parsed = json.loads(result)
        assert parsed["status"] == "done"
        assert parsed["data"]["id"] == "test-123"

    @pytest.mark.asyncio
    async def test_flow_with_cursor(self, mock_keeper):
        from keep.mcp import keep_flow
        from keep.state_doc_runtime import FlowResult
        mock_keeper.run_flow_command.return_value = FlowResult(
            status="stopped", ticks=3, cursor="abc123",
            data={"reason": "budget"}, tried_queries=["test query"],
        )
        result = await keep_flow(
            state="query-resolve", params={"query": "test"}, budget=3,
        )
        parsed = json.loads(result)
        assert parsed["status"] == "stopped"
        assert parsed["cursor"] == "abc123"
        assert parsed["tried_queries"] == ["test query"]
        assert "bindings" not in parsed
        assert "history" not in parsed

    @pytest.mark.asyncio
    async def test_flow_error(self, mock_keeper):
        from keep.mcp import keep_flow
        mock_keeper.run_flow_command.side_effect = ValueError("bad params")
        result = await keep_flow(state="put")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_flow_no_data_in_output(self, mock_keeper):
        from keep.mcp import keep_flow
        from keep.state_doc_runtime import FlowResult
        mock_keeper.run_flow_command.return_value = FlowResult(
            status="done", ticks=1,
        )
        result = await keep_flow(state="delete", params={"id": "x"})
        parsed = json.loads(result)
        assert "data" not in parsed


# ---------------------------------------------------------------------------
# keep_prompt
# ---------------------------------------------------------------------------

class TestKeepPrompt:

    @pytest.mark.asyncio
    async def test_list_prompts(self, mock_keeper):
        from keep.mcp import keep_prompt
        mock_keeper.list_prompts.return_value = [
            PromptInfo(name="reflect", summary="The reflection practice"),
            PromptInfo(name="session-start", summary="Session startup"),
        ]
        result = await keep_prompt()
        assert "reflect" in result
        assert "session-start" in result

    @pytest.mark.asyncio
    async def test_render_prompt(self, mock_keeper):
        from keep.mcp import keep_prompt
        mock_keeper.render_prompt.return_value = PromptResult(
            prompt="Reflect on {get}",
            context=None,
            search_results=[],
        )
        result = await keep_prompt(name="reflect")
        assert "Reflect on" in result

    @pytest.mark.asyncio
    async def test_prompt_not_found(self, mock_keeper):
        from keep.mcp import keep_prompt
        mock_keeper.render_prompt.return_value = None
        result = await keep_prompt(name="nonexistent")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# keep_help
# ---------------------------------------------------------------------------

class TestKeepHelp:

    @pytest.mark.asyncio
    async def test_help_index(self):
        from keep.mcp import keep_help
        result = await keep_help(topic="index")
        assert "quickstart" in result.lower() or "guide" in result.lower()

    @pytest.mark.asyncio
    async def test_help_specific_topic(self):
        from keep.mcp import keep_help
        result = await keep_help(topic="flow-actions")
        assert "find" in result.lower()
