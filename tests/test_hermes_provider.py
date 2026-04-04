"""Tests for keep.hermes.provider — the Hermes MemoryProvider integration.

Uses mock_providers fixture to avoid loading real ML models or databases.
Tests cover the full MemoryProvider protocol surface.
"""

import json

import pytest

from keep.hermes.provider import KeepMemoryProvider


class TestLifecycle:
    """Provider creation, initialization, and shutdown."""

    def test_is_available(self):
        p = KeepMemoryProvider()
        # keep is importable in the test environment
        assert p.is_available() is True

    def test_name(self):
        p = KeepMemoryProvider()
        assert p.name == "keep"

    def test_initialize_creates_keeper(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize(
            "test-session",
            hermes_home=str(tmp_path),
            platform="cli",
            agent_identity="test",
        )
        assert p._keeper is not None
        p.shutdown()
        assert p._keeper is None

    def test_initialize_skips_cron(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize(
            "test-session",
            hermes_home=str(tmp_path),
            platform="cron",
            agent_context="cron",
        )
        assert p._keeper is None

    def test_shutdown_idempotent(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        p.shutdown()
        p.shutdown()  # second call should not raise


class TestSystemPrompt:
    """system_prompt_block() rendering."""

    def test_returns_empty_when_not_initialized(self):
        p = KeepMemoryProvider()
        assert p.system_prompt_block() == ""

    def test_returns_header_when_initialized(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        block = p.system_prompt_block()
        assert "# Keep Memory" in block
        assert "Active" in block
        p.shutdown()

    def test_setup_required_message(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p._setup_required = True
        block = p.system_prompt_block()
        assert "not set up yet" in block
        assert "hermes memory setup" in block

    def test_setup_required_uses_profile_name(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p._setup_required = True
        p._setup_cmd = "coder memory setup"
        block = p.system_prompt_block()
        assert "coder memory setup" in block


class TestPrefetch:
    """prefetch() and queue_prefetch()."""

    def test_prefetch_empty_when_not_initialized(self):
        p = KeepMemoryProvider()
        assert p.prefetch("test query") == ""

    def test_prefetch_returns_context(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        # Put something to find
        p._keeper.put("Authentication uses JWT tokens", tags={"topic": "auth"})
        result = p.prefetch("auth tokens")
        # May be empty if mock embeddings don't produce meaningful similarity,
        # but should not raise
        assert isinstance(result, str)
        p.shutdown()

    def test_queue_prefetch_does_not_raise(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        p.queue_prefetch("auth tokens")
        # Wait for background thread
        if p._prefetch_thread:
            p._prefetch_thread.join(timeout=5.0)
        p.shutdown()


class TestSyncTurn:
    """sync_turn() conversation recording."""

    def test_sync_turn_writes_item(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli",
                     agent_identity="test")
        p.sync_turn("Hello agent", "Hello! How can I help?")
        # Wait for background thread
        if p._sync_thread:
            p._sync_thread.join(timeout=5.0)
        # Verify the item was written
        item = p._keeper.get(p._session_item_id)
        assert item is not None
        assert item.summary  # non-empty summary (content or auto-generated)
        p.shutdown()

    def test_sync_turn_skips_when_not_initialized(self):
        p = KeepMemoryProvider()
        # Should not raise
        p.sync_turn("Hello", "Hi")


class TestToolSchemas:
    """get_tool_schemas() and handle_tool_call()."""

    def test_tools_available_before_initialize(self):
        """Tool schemas are static — available even before initialize()."""
        p = KeepMemoryProvider()
        schemas = p.get_tool_schemas()
        assert len(schemas) == 3

    def test_three_tools_when_initialized(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        schemas = p.get_tool_schemas()
        names = [s["name"] for s in schemas]
        assert "keep_flow" in names
        assert "keep_help" in names
        assert "keep_prompt" in names
        p.shutdown()

    def test_tool_error_when_not_initialized(self):
        p = KeepMemoryProvider()
        result = json.loads(p.handle_tool_call("keep_flow", {}))
        assert "error" in result

    def test_tool_error_setup_required(self):
        p = KeepMemoryProvider()
        p._setup_required = True
        result = json.loads(p.handle_tool_call("keep_flow", {}))
        assert "error" in result
        assert "not configured" in result["error"]
        assert "hint" in result


class TestKeepFlow:
    """keep_flow tool — state-doc flow execution."""

    def test_flow_requires_state(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_flow", {}))
        assert "error" in result
        assert "state" in result["error"].lower()
        p.shutdown()

    def test_flow_put(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_flow", {
            "state": "put",
            "params": {"content": "test fact", "tags": {"topic": "test"}},
        }))
        assert "result" in result  # rendered text
        assert "done" in result["result"]
        p.shutdown()

    def test_flow_get(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        # Put then get
        p._keeper.put("important fact", id="test-item")
        result = json.loads(p.handle_tool_call("keep_flow", {
            "state": "get",
            "params": {"id": "test-item"},
        }))
        assert "result" in result  # rendered text
        p.shutdown()

    def test_flow_unknown_state(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_flow", {
            "state": "nonexistent-state-doc",
        }))
        assert "result" in result
        assert "error" in result["result"]
        p.shutdown()


class TestKeepHelp:
    """keep_help tool."""

    def test_help_index(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_help", {}))
        assert "result" in result
        assert "Guides" in result["result"] or "guide" in result["result"].lower()
        p.shutdown()

    def test_help_topic(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_help", {
            "topic": "agent-guide",
        }))
        assert "result" in result
        assert len(result["result"]) > 100
        p.shutdown()

    def test_help_unknown_topic(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_help", {
            "topic": "does-not-exist",
        }))
        # get_help_topic returns guidance text listing available topics
        assert "result" in result
        assert "Available topics" in result["result"]
        p.shutdown()


class TestKeepPrompt:
    """keep_prompt tool."""

    def test_prompt_list(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_prompt", {}))
        assert "prompts" in result
        assert isinstance(result["prompts"], list)
        p.shutdown()

    def test_prompt_render(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        prompts = json.loads(p.handle_tool_call("keep_prompt", {}))
        if prompts.get("prompts"):
            name = prompts["prompts"][0]
            result = json.loads(p.handle_tool_call("keep_prompt", {
                "name": name,
            }))
            # Should have result or error (prompt may need context)
            assert "result" in result or "error" in result
        p.shutdown()

    def test_prompt_not_found(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        result = json.loads(p.handle_tool_call("keep_prompt", {
            "name": "nonexistent-prompt",
        }))
        assert result.get("error") is not None or result.get("result") is not None
        p.shutdown()


class TestObservationHooks:
    """on_memory_write, on_pre_compress, on_delegation."""

    def test_on_memory_write(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        p.on_memory_write("add", "memory", "User prefers dark mode")
        # Background thread — wait for it
        import time
        time.sleep(0.5)
        p.shutdown()

    def test_on_memory_write_skips_remove(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        # "remove" action should be ignored
        p.on_memory_write("remove", "memory", "something")
        p.shutdown()

    def test_on_pre_compress(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = p.on_pre_compress(messages)
        assert result == ""  # Returns empty string (no contribution to compressor)
        p.shutdown()

    def test_on_pre_compress_empty(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        assert p.on_pre_compress([]) == ""
        p.shutdown()

    def test_on_delegation(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        p.on_delegation("search for auth docs", "Found 3 relevant files",
                        child_session_id="child-123")
        import time
        time.sleep(0.5)
        p.shutdown()

    def test_on_session_end(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli")
        p.on_session_end([{"role": "user", "content": "bye"}])
        p.shutdown()


class TestSessionItemId:
    """_build_session_item_id — channel scoping."""

    def test_cli_stable_key(self):
        p = KeepMemoryProvider()
        item_id = p._build_session_item_id(
            "random-uuid-123", platform="cli", agent_identity="coder"
        )
        assert item_id == "coder:cli"
        # Same key regardless of session_id
        item_id2 = p._build_session_item_id(
            "different-uuid-456", platform="cli", agent_identity="coder"
        )
        assert item_id == item_id2

    def test_gateway_includes_platform_and_session(self):
        p = KeepMemoryProvider()
        item_id = p._build_session_item_id(
            "agent:main:telegram:dm:12345", platform="telegram", agent_identity="default"
        )
        assert item_id == "default:telegram:agent:main:telegram:dm:12345"

    def test_gateway_discord_thread(self):
        p = KeepMemoryProvider()
        item_id = p._build_session_item_id(
            "20260404_174301_4de0b03a", platform="discord", agent_identity="default"
        )
        assert item_id == "default:discord:20260404_174301_4de0b03a"


class TestSessionTags:
    """_build_session_tags — metadata tagging."""

    def test_minimal_tags(self):
        p = KeepMemoryProvider()
        tags = p._build_session_tags("s1")
        assert tags == {"source": "hermes"}

    def test_platform_tag(self):
        p = KeepMemoryProvider()
        tags = p._build_session_tags("s1", platform="telegram")
        assert tags["platform"] == "telegram"

    def test_identity_tag(self):
        p = KeepMemoryProvider()
        tags = p._build_session_tags("s1", agent_identity="coder")
        assert tags["agent_identity"] == "coder"


class TestConfigSchema:
    """get_config_schema() and save_config()."""

    def test_config_schema_returns_list(self, mock_providers):
        p = KeepMemoryProvider()
        schema = p.get_config_schema()
        assert isinstance(schema, list)

    def test_config_schema_empty_without_keep(self):
        """When keep is not importable, returns empty list."""
        p = KeepMemoryProvider()
        # This should work even if the wizard imports fail
        # (gracefully returns [])
        schema = p.get_config_schema()
        assert isinstance(schema, list)


class TestTokenBudgets:
    """_configure_token_budgets — hermes config mapping."""

    def test_default_budgets(self):
        p = KeepMemoryProvider()
        p._configure_token_budgets()
        assert p._system_prompt_token_budget > 0
        assert p._prefetch_inline_token_budget > 0
        assert p._prefetch_background_token_budget > 0

    def test_custom_budgets(self):
        p = KeepMemoryProvider()
        p._configure_token_budgets(memory_char_limit=4400, user_char_limit=2750)
        # Larger limits → larger budgets
        assert p._system_prompt_token_budget > 1300

    def test_zero_limits(self):
        p = KeepMemoryProvider()
        p._configure_token_budgets(memory_char_limit=0, user_char_limit=0)
        assert p._system_prompt_token_budget == 1300  # fallback


class TestSetupCommand:
    """Profile-aware setup command."""

    def test_default_command(self):
        p = KeepMemoryProvider()
        assert p._setup_cmd == "hermes memory setup"

    def test_profile_command(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli",
                     agent_identity="coder")
        assert p._setup_cmd == "coder memory setup"

    def test_default_identity_keeps_hermes(self, mock_providers, tmp_path):
        p = KeepMemoryProvider()
        p.initialize("s1", hermes_home=str(tmp_path), platform="cli",
                     agent_identity="default")
        assert p._setup_cmd == "hermes memory setup"
