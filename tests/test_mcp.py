"""
Tests for the MCP stdio server tool functions.

Tests the tool layer in isolation by mocking HTTP calls to the daemon —
verifies parameter mapping, return formatting, and edge cases for the
three tools: keep_flow, keep_prompt, keep_help.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_daemon():
    """Patch _ensure_daemon and http_request to avoid real daemon."""
    with patch("keep.mcp._ensure_daemon", return_value=9999), \
         patch("keep.mcp.http_request") as mock_http:
        yield mock_http


# ---------------------------------------------------------------------------
# keep_flow
# ---------------------------------------------------------------------------

class TestKeepFlow:

    @pytest.mark.asyncio
    async def test_flow_returns_json(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {"id": "test-123"},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_flow(state="put", params={"content": "hello"})
        parsed = json.loads(result)
        assert parsed["status"] == "done"
        assert parsed["data"]["id"] == "test-123"

    @pytest.mark.asyncio
    async def test_flow_with_cursor(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "stopped", "ticks": 3,
            "data": {"reason": "budget"}, "cursor": "abc123",
            "tried_queries": ["test query"],
            "bindings": {}, "history": [],
        })
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
    async def test_flow_error(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (500, {"error": "bad params"})
        result = await keep_flow(state="put")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_flow_no_data_in_output(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": None, "bindings": {}, "history": [],
            "cursor": None, "tried_queries": [],
        })
        result = await keep_flow(state="delete", params={"id": "x"})
        parsed = json.loads(result)
        assert "data" not in parsed

    @pytest.mark.asyncio
    async def test_flow_with_token_budget(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {}, "bindings": {}, "history": [],
            "cursor": None, "tried_queries": [],
            "rendered": "Rendered output text",
        })
        result = await keep_flow(state="query-resolve", token_budget=4000)
        assert result == "Rendered output text"
        # Verify token_budget was sent in the request
        call_body = mock_daemon.call_args[0][3]  # body arg
        assert call_body.get("token_budget") == 4000


# ---------------------------------------------------------------------------
# keep_prompt
# ---------------------------------------------------------------------------

class TestKeepPrompt:

    @pytest.mark.asyncio
    async def test_list_prompts(self, mock_daemon):
        from keep.mcp import keep_prompt
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {
                "prompts": [
                    {"name": "reflect", "summary": "The reflection practice"},
                    {"name": "session-start", "summary": "Session startup"},
                ],
            },
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_prompt()
        assert "reflect" in result
        assert "session-start" in result

    @pytest.mark.asyncio
    async def test_render_prompt(self, mock_daemon):
        from keep.mcp import keep_prompt
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {"text": "Reflect on your recent work..."},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_prompt(name="reflect")
        assert "Reflect on" in result

    @pytest.mark.asyncio
    async def test_prompt_not_found(self, mock_daemon):
        from keep.mcp import keep_prompt
        mock_daemon.return_value = (200, {
            "status": "error", "ticks": 1,
            "data": {"error": "prompt not found: nonexistent"},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
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
