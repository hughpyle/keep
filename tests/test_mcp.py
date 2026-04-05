"""Tests for the MCP stdio server tool functions.

Tests the tool layer in isolation by mocking HTTP calls to the daemon —
verifies parameter mapping, return formatting, and edge cases for the
three tools: keep_flow, keep_prompt, keep_help.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from keep.const import STATE_DELETE, STATE_PUT, STATE_QUERY_RESOLVE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_daemon():
    """Patch _ensure_daemon and http_request to avoid real daemon."""
    with patch("keep.mcp._ensure_daemon", return_value=9999), \
         patch("keep.mcp.http_request") as mock_http:
        yield mock_http


async def _keep_flow_schema(server):
    for tool in await server.list_tools():
        if tool.name == "keep_flow":
            return tool.inputSchema
    raise AssertionError("keep_flow schema not found")


# ---------------------------------------------------------------------------
# keep_flow
# ---------------------------------------------------------------------------

class TestKeepFlow:
    """Tests for MCP keep-flow endpoint."""

    def test_flow_schema_exposes_common_param_keys(self):
        from keep.mcp import mcp

        schema = asyncio.run(_keep_flow_schema(mcp))
        params_ref = schema["properties"]["params"]["anyOf"][0]["$ref"]
        params_schema = schema
        for part in params_ref.removeprefix("#/").split("/"):
            params_schema = params_schema[part]

        assert "properties" in params_schema
        assert "item_id" in params_schema["properties"]
        assert "query" in params_schema["properties"]
        assert "content" in params_schema["properties"]
        assert params_schema["additionalProperties"] is True
        assert schema["properties"]["params"]["examples"][0] == {"item_id": "now"}

    @pytest.mark.asyncio
    async def test_flow_returns_json(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {"id": "test-123"},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_flow(state=STATE_PUT, params={"content": "hello"})
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
            state=STATE_QUERY_RESOLVE, params={"query": "test"}, budget=3,
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
        result = await keep_flow(state=STATE_PUT)
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_flow_no_data_in_output(self, mock_daemon):
        from keep.mcp import keep_flow
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": None, "bindings": {}, "history": [],
            "cursor": None, "tried_queries": [],
        })
        result = await keep_flow(state=STATE_DELETE, params={"id": "x"})
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
        result = await keep_flow(state=STATE_QUERY_RESOLVE, token_budget=4000)
        assert result == "Rendered output text"
        # Verify token_budget was sent in the request
        call_body = mock_daemon.call_args[0][3]  # body arg
        assert call_body.get("token_budget") == 4000

    @pytest.mark.asyncio
    async def test_flow_retries_after_connection_refused(self):
        from keep.mcp import keep_flow

        with (
            patch("keep.mcp._ensure_daemon", side_effect=[9999, 10000]),
            patch("keep.mcp.http_request") as mock_http,
        ):
            mock_http.side_effect = [
                ConnectionRefusedError(61, "refused"),
                (200, {
                    "status": "done", "ticks": 1,
                    "data": {"ok": True},
                    "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
                }),
            ]

            result = await keep_flow(state=STATE_PUT, params={"content": "hello"})

        parsed = json.loads(result)
        assert parsed["status"] == "done"
        assert mock_http.call_args_list[0].args[1] == 9999
        assert mock_http.call_args_list[1].args[1] == 10000


# ---------------------------------------------------------------------------
# keep_prompt
# ---------------------------------------------------------------------------

class TestKeepPrompt:
    """Tests for MCP keep-prompt endpoint."""

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
        assert result.structuredContent == {
            "mode": "list",
            "prompts": [
                {"name": "reflect", "summary": "The reflection practice"},
                {"name": "session-start", "summary": "Session startup"},
            ],
        }
        assert "reflect" in result.content[0].text
        assert "session-start" in result.content[0].text

    @pytest.mark.asyncio
    async def test_render_prompt(self, mock_daemon):
        from keep.mcp import keep_prompt
        mock_daemon.return_value = (200, {
            "status": "done", "ticks": 1,
            "data": {"text": "Reflect on your recent work..."},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_prompt(name="reflect")
        assert result.structuredContent == {
            "mode": "render",
            "name": "reflect",
            "text": "Reflect on your recent work...",
        }
        assert "Reflect on" in result.content[0].text

    @pytest.mark.asyncio
    async def test_prompt_not_found(self, mock_daemon):
        from keep.mcp import keep_prompt
        mock_daemon.return_value = (200, {
            "status": "error", "ticks": 1,
            "data": {"error": "prompt not found: nonexistent"},
            "bindings": {}, "history": [], "cursor": None, "tried_queries": [],
        })
        result = await keep_prompt(name="nonexistent")
        assert result.isError is True
        assert result.structuredContent == {
            "mode": "render",
            "name": "nonexistent",
            "error": "prompt not found: nonexistent",
        }
        assert "not found" in result.content[0].text.lower()

    @pytest.mark.asyncio
    async def test_prompt_http_error_returns_structured_error(self, mock_daemon):
        from keep.mcp import keep_prompt

        mock_daemon.return_value = (500, {"error": "bad upstream"})

        result = await keep_prompt(name="reflect")

        assert result.isError is True
        assert result.structuredContent == {
            "mode": "error",
            "error": "Error: bad upstream",
        }
        assert result.content[0].text == "Error: bad upstream"


class TestMCPPromptExposure:
    """Tests for MCP-native prompt exposure."""

    @pytest.mark.asyncio
    async def test_list_prompts_filters_to_mcp_exposed_prompts(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {
            "status": "done",
            "ticks": 1,
            "data": {
                "prompts": [
                    {
                        "name": "reflect",
                        "summary": "The reflection practice",
                        "mcp_arguments": ["text", "id", "since", "token_budget"],
                    },
                    {
                        "name": "session-start",
                        "summary": "Session startup",
                    },
                ],
            },
            "bindings": {},
            "history": [],
            "cursor": None,
            "tried_queries": [],
        })

        prompts = await mcp.list_prompts()

        assert len(prompts) == 1
        assert prompts[0].name == "reflect"
        assert [arg.name for arg in (prompts[0].arguments or [])] == [
            "text",
            "id",
            "since",
            "token_budget",
        ]
        assert all(arg.required is False for arg in (prompts[0].arguments or []))

    @pytest.mark.asyncio
    async def test_list_prompts_returns_empty_when_daemon_is_unavailable(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.side_effect = ConnectionRefusedError(61, "refused")

        prompts = await mcp.list_prompts()

        assert prompts == []

    @pytest.mark.asyncio
    async def test_get_prompt_renders_via_existing_prompt_flow(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {
            "status": "done",
            "ticks": 1,
            "data": {"text": "Rendered reflect prompt"},
            "bindings": {},
            "history": [],
            "cursor": None,
            "tried_queries": [],
        })

        result = await mcp.get_prompt(
            "reflect",
            {"text": "auth", "id": "now", "since": "P7D", "ignored": "x"},
        )

        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert result.messages[0].content.text == "Rendered reflect prompt"
        render_body = mock_daemon.call_args.args[3]
        assert render_body["params"] == {
            "name": "reflect",
            "text": "auth",
            "id": "now",
            "since": "P7D",
        }

    @pytest.mark.asyncio
    async def test_get_prompt_ignores_empty_string_optional_args(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {
            "status": "done",
            "ticks": 1,
            "data": {"text": "Rendered reflect prompt"},
            "bindings": {},
            "history": [],
            "cursor": None,
            "tried_queries": [],
        })

        await mcp.get_prompt(
            "reflect",
            {"text": "", "id": "", "since": "  ", "token_budget": ""},
        )

        render_body = mock_daemon.call_args.args[3]
        assert render_body["params"] == {"name": "reflect"}

    @pytest.mark.asyncio
    async def test_get_prompt_raises_on_unknown_prompt(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {
            "status": "error",
            "ticks": 1,
            "data": {"error": "prompt not found: nonexistent"},
            "bindings": {},
            "history": [],
            "cursor": None,
            "tried_queries": [],
        })

        with pytest.raises(ValueError, match="prompt not found: nonexistent"):
            await mcp.get_prompt("nonexistent")


# ---------------------------------------------------------------------------
# MCP resources
# ---------------------------------------------------------------------------

class TestMCPResources:
    """Tests for MCP resource and template exposure."""

    @pytest.mark.asyncio
    async def test_list_resources_includes_now(self, mock_daemon):
        from keep.mcp import mcp

        resources = await mcp.list_resources()

        assert any(str(resource.uri) == "keep://now" for resource in resources)

    @pytest.mark.asyncio
    async def test_list_resource_templates_includes_note_template(self, mock_daemon):
        from keep.mcp import mcp

        templates = await mcp.list_resource_templates()

        assert any(template.uriTemplate == "keep://{id}" for template in templates)

    @pytest.mark.asyncio
    async def test_read_now_resource_returns_note_json(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {"id": "now", "summary": "Current note", "tags": {}})

        contents = await mcp.read_resource("keep://now")

        assert len(contents) == 1
        assert contents[0].mime_type == "application/json"
        data = json.loads(contents[0].content)
        assert data["id"] == "now"
        assert mock_daemon.call_args.args[0] == "GET"
        assert mock_daemon.call_args.args[2] == "/v1/notes/now"

    @pytest.mark.asyncio
    async def test_read_template_resource_decodes_note_id(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (
            200,
            {"id": "file:///tmp/note.md", "summary": "File note", "tags": {}},
        )

        contents = await mcp.read_resource("keep://file%3A%2F%2F%2Ftmp%2Fnote.md")

        assert len(contents) == 1
        data = json.loads(contents[0].content)
        assert data["id"] == "file:///tmp/note.md"
        assert mock_daemon.call_args.args[2] == "/v1/notes/file%3A%2F%2F%2Ftmp%2Fnote.md"


class TestMCPToolDescriptions:
    """Tests for dynamic MCP tool descriptions."""

    @pytest.mark.asyncio
    async def test_keep_prompt_tool_description_lists_available_prompts(self, mock_daemon):
        from keep.mcp import mcp

        mock_daemon.return_value = (200, {
            "status": "done",
            "ticks": 1,
            "data": {
                "prompts": [
                    {"name": "reflect", "summary": "Reflect"},
                    {"name": "conversation", "summary": "Conversation"},
                    {"name": "query", "summary": "Query"},
                ],
            },
            "bindings": {},
            "history": [],
            "cursor": None,
            "tried_queries": [],
        })

        tools = await mcp.list_tools()
        keep_prompt_tool = next(tool for tool in tools if tool.name == "keep_prompt")

        assert keep_prompt_tool.description is not None
        assert "Available prompts:" in keep_prompt_tool.description
        assert "reflect" in keep_prompt_tool.description
        assert "conversation" in keep_prompt_tool.description
        assert "query" in keep_prompt_tool.description


# ---------------------------------------------------------------------------
# keep_help
# ---------------------------------------------------------------------------

class TestKeepHelp:
    """Tests for MCP keep-help endpoint."""

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
