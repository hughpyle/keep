"""
Tests for the enhanced `keep config` command.

Tests verify:
1. Path-based config access (file, tool, store, providers.*)
2. JSON output format
3. Human-readable output with commented defaults
4. Shell scripting support (raw output for single values)

These tests do NOT invoke any ML providers or models.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def cli():
    """Run CLI command and return result."""
    def run(*args: str, input: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "keep", *args],
            capture_output=True,
            text=True,
            input=input,
            cwd=Path(__file__).parent.parent,
        )
    return run


@pytest.fixture
def mock_keeper():
    """Create a mock Keeper with predictable config values."""
    from keep.config import ProviderConfig, StoreConfig

    mock_kp = MagicMock()
    mock_kp._store_path = Path("/test/store")
    mock_kp.list_collections.return_value = ["default", "notes"]

    # Create a real StoreConfig for predictable behavior
    cfg = StoreConfig(
        path=Path("/test/store"),
        config_dir=Path("/test/config"),
        embedding=ProviderConfig("sentence-transformers"),
        summarization=ProviderConfig("truncate"),
        document=ProviderConfig("composite"),
        default_tags={"project": "testproject"},
    )
    mock_kp._config = cfg

    return mock_kp


# -----------------------------------------------------------------------------
# get_tool_directory Tests
# -----------------------------------------------------------------------------

class TestGetToolDirectory:
    """Tests for the get_tool_directory function."""

    def test_returns_path(self):
        """get_tool_directory returns a Path."""
        from keep.config import get_tool_directory
        result = get_tool_directory()
        assert isinstance(result, Path)

    def test_skill_md_exists(self):
        """SKILL.md exists in the tool directory."""
        from keep.config import get_tool_directory
        tool_dir = get_tool_directory()
        skill_path = tool_dir / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"

    def test_contains_skill_and_relates_to_package(self):
        """Tool directory contains SKILL.md and relates to keep package."""
        from keep.config import get_tool_directory
        import keep

        tool_dir = get_tool_directory()
        keep_pkg = Path(keep.__file__).parent

        # SKILL.md must exist in tool directory
        assert (tool_dir / "SKILL.md").exists()

        # Tool directory is either the package itself (installed wheel)
        # or its parent (development install)
        assert tool_dir == keep_pkg or tool_dir == keep_pkg.parent


# -----------------------------------------------------------------------------
# _get_config_value Tests
# -----------------------------------------------------------------------------

class TestGetConfigValue:
    """Tests for the _get_config_value helper function."""

    def test_file_path(self, mock_keeper):
        """'file' returns config file path."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "file")
        assert result == "/test/config/keep.toml"

    def test_tool_path(self, mock_keeper):
        """'tool' returns tool directory."""
        from keep.cli import _get_config_value
        from keep.config import get_tool_directory
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "tool")
        assert result == str(get_tool_directory())

    def test_store_path(self, mock_keeper):
        """'store' returns store path."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "store")
        assert result == "/test/store"

    def test_collections(self, mock_keeper):
        """'collections' returns list (may be empty without real store)."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "collections")
        # With mock store path, returns empty list
        assert isinstance(result, list)

    def test_providers(self, mock_keeper):
        """'providers' returns dict of provider names."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "providers")
        assert result == {
            "embedding": "sentence-transformers",
            "summarization": "truncate",
            "document": "composite",
        }

    def test_providers_embedding(self, mock_keeper):
        """'providers.embedding' returns embedding provider name."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "providers.embedding")
        assert result == "sentence-transformers"

    def test_providers_summarization(self, mock_keeper):
        """'providers.summarization' returns summarization provider name."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "providers.summarization")
        assert result == "truncate"

    def test_providers_document(self, mock_keeper):
        """'providers.document' returns document provider name."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "providers.document")
        assert result == "composite"

    def test_tags(self, mock_keeper):
        """'tags' returns default tags dict."""
        from keep.cli import _get_config_value
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "tags")
        assert result == {"project": "testproject"}

    def test_invalid_path_raises(self, mock_keeper):
        """Invalid path raises BadParameter."""
        import typer
        from keep.cli import _get_config_value
        with pytest.raises(typer.BadParameter) as exc_info:
            _get_config_value(mock_keeper._config, mock_keeper._store_path, "invalid.path")
        assert "Unknown config path" in str(exc_info.value)


# -----------------------------------------------------------------------------
# CLI Integration Tests (subprocess)
# -----------------------------------------------------------------------------

class TestConfigCommand:
    """Tests for the config command via CLI."""

    def test_config_no_args(self, cli):
        """'config' with no args shows full config."""
        result = cli("config")
        assert result.returncode == 0
        assert "file:" in result.stdout
        assert "tool:" in result.stdout
        assert "store:" in result.stdout
        assert "providers:" in result.stdout

    def test_config_help(self, cli):
        """'config --help' shows help with examples."""
        result = cli("config", "--help")
        assert result.returncode == 0
        # Check for key terms (help text may wrap lines differently)
        assert "config" in result.stdout
        assert "file" in result.stdout
        assert "tool" in result.stdout
        assert "providers" in result.stdout
        assert "PATH" in result.stdout

    def test_config_file(self, cli):
        """'config file' returns config file path."""
        result = cli("config", "file")
        assert result.returncode == 0
        assert "keep.toml" in result.stdout

    def test_config_tool(self, cli):
        """'config tool' returns tool directory."""
        result = cli("config", "tool")
        assert result.returncode == 0
        # Verify SKILL.md exists there
        tool_path = Path(result.stdout.strip())
        assert (tool_path / "SKILL.md").exists()

    def test_config_store(self, cli):
        """'config store' returns store path."""
        result = cli("config", "store")
        assert result.returncode == 0
        # Output should be a path
        assert "/" in result.stdout or "\\" in result.stdout

    def test_config_collections(self, cli):
        """'config collections' returns JSON list."""
        result = cli("config", "collections")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list)

    def test_config_providers(self, cli):
        """'config providers' returns JSON dict of providers."""
        result = cli("config", "providers")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "embedding" in parsed
        assert "summarization" in parsed
        assert "document" in parsed

    def test_config_providers_embedding(self, cli):
        """'config providers.embedding' returns provider name."""
        result = cli("config", "providers.embedding")
        assert result.returncode == 0
        # Should be a simple string (provider name)
        name = result.stdout.strip()
        assert name  # Not empty
        assert "{" not in name  # Not JSON

    def test_config_invalid_path(self, cli):
        """'config invalid.path' returns error."""
        result = cli("config", "invalid.path.here")
        assert result.returncode == 1
        assert "Unknown config path" in result.stderr

    def test_config_json_full(self, cli):
        """'--json config' returns complete JSON object."""
        result = cli("--json", "config")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "file" in parsed
        assert "tool" in parsed
        assert "store" in parsed
        assert "collections" in parsed
        assert "providers" in parsed
        assert isinstance(parsed["providers"], dict)

    def test_config_json_path(self, cli):
        """'--json config store' wraps value in object."""
        result = cli("--json", "config", "store")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "store" in parsed
        assert isinstance(parsed["store"], str)

    def test_config_json_providers(self, cli):
        """'--json config providers' wraps value in object."""
        result = cli("--json", "config", "providers")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "providers" in parsed
        assert isinstance(parsed["providers"], dict)


# -----------------------------------------------------------------------------
# Shell Scripting Composability Tests
# -----------------------------------------------------------------------------

class TestConfigShellScripting:
    """Tests for shell scripting use cases."""

    def test_tool_path_usable_in_ls(self, cli):
        """Tool path can be used in shell commands like ls."""
        # Get the tool path
        result = cli("config", "tool")
        assert result.returncode == 0
        tool_path = result.stdout.strip()

        # Verify it's a valid directory
        assert Path(tool_path).is_dir()

        # Verify ls would work (SKILL.md is there)
        assert (Path(tool_path) / "SKILL.md").exists()

    def test_store_path_usable(self, cli):
        """Store path output is a valid path string."""
        result = cli("config", "store")
        assert result.returncode == 0
        store_path = result.stdout.strip()

        # Should be a path (not JSON, not with quotes)
        assert not store_path.startswith('"')
        assert not store_path.startswith('{')

    def test_single_value_no_newline_json(self, cli):
        """Single value outputs without extra JSON structure."""
        result = cli("config", "providers.embedding")
        assert result.returncode == 0
        output = result.stdout.strip()

        # Should be just the provider name, not wrapped in JSON
        assert output  # Not empty
        # Should not start with { (that would be JSON object)
        if output.startswith("{"):
            pytest.fail(f"Expected raw value, got JSON: {output}")


# -----------------------------------------------------------------------------
# Commented Defaults Output Tests
# -----------------------------------------------------------------------------

class TestConfigCommentedDefaults:
    """Tests for commented defaults in full config output."""

    def test_shows_commented_tags_when_empty(self, cli):
        """Shows commented tags example when no tags configured."""
        result = cli("config")
        assert result.returncode == 0

        # If there are no tags, should show commented example
        if "tags:" not in result.stdout or "# tags:" in result.stdout:
            assert "# tags:" in result.stdout or "tags:" in result.stdout
            # Should have example keys
            if "# tags:" in result.stdout:
                assert "#   project:" in result.stdout or "project" in result.stdout

    def test_shows_all_providers(self, cli):
        """Full config shows embedding and summarization providers."""
        result = cli("config")
        assert result.returncode == 0
        assert "embedding:" in result.stdout
        assert "summarization:" in result.stdout
        # Note: document provider is internal (always composite), not shown


# -----------------------------------------------------------------------------
# Edge Cases
# -----------------------------------------------------------------------------

class TestConfigEdgeCases:
    """Edge case tests for config command."""

    def test_tags_empty_returns_empty_dict(self, cli):
        """'config tags' returns empty dict when no tags configured."""
        result = cli("config", "tags")
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        # Should be a dict (possibly empty)
        assert isinstance(parsed, dict)

    def test_nested_attribute_access(self, mock_keeper):
        """Can access nested config attributes."""
        from keep.cli import _get_config_value

        # Access embedding.name (ProviderConfig attribute)
        result = _get_config_value(mock_keeper._config, mock_keeper._store_path, "embedding")
        # Should return the provider name (due to hasattr name check)
        assert result == "sentence-transformers"

    def test_no_config_file_returns_none(self):
        """'file' returns None when no config loaded."""
        from keep.cli import _get_config_value

        result = _get_config_value(None, Path("/test/store"), "file")
        assert result is None

    def test_providers_no_config_returns_none(self):
        """'providers' returns None when no config loaded."""
        from keep.cli import _get_config_value

        result = _get_config_value(None, Path("/test/store"), "providers")
        assert result is None
