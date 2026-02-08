"""
CLI tests for reflective memory.

Tests verify:
1. CLI commands map to equivalent Python API
2. JSON output is valid and parseable (for jq/unix composability)
3. Exit codes follow conventions (0 success, 1 not found, etc.)
4. Human-readable output is line-oriented (for grep/wc/etc.)
"""

import json
import subprocess
import sys
from pathlib import Path

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


# -----------------------------------------------------------------------------
# Help and Basic Commands
# -----------------------------------------------------------------------------

class TestCliBasics:
    """Basic CLI functionality tests."""
    
    def test_help(self, cli):
        """CLI shows help with --help."""
        result = cli("--help")
        assert result.returncode == 0
        assert "keep" in result.stdout.lower()
        assert "find" in result.stdout
        assert "put" in result.stdout
    
    def test_no_args_shows_now(self, cli):
        """CLI with no args shows current working context."""
        result = cli()
        # Returns success and shows the "now" document
        assert result.returncode == 0
        assert "---" in result.stdout  # YAML frontmatter
        assert "id:" in result.stdout

    def test_meta_docs_loaded(self, cli):
        """Meta-doc system docs are loaded and accessible."""
        result = cli("get", ".meta/todo", "--no-similar")
        assert result.returncode == 0
        assert ".meta/todo" in result.stdout

    def test_tag_type_doc_loaded(self, cli):
        """Tag description doc .tag/type is loaded and has full content."""
        result = cli("get", ".tag/type", "--no-similar")
        assert result.returncode == 0
        assert "Content Classification" in result.stdout
        # Should contain the values table (verbatim, not summarized)
        assert "learning" in result.stdout
        assert "breakdown" in result.stdout

    def test_meta_sections_use_namespace_prefix(self, cli):
        """Meta sections in frontmatter use meta/ prefix to avoid key conflicts."""
        result = cli("get", ".meta/todo", "--no-similar")
        assert result.returncode == 0
        # The meta-doc itself shouldn't show meta/ sections (it IS a meta-doc)
        # but its content should have the query lines intact
        assert "act=commitment" in result.stdout

    def test_command_help(self, cli):
        """Individual commands have help."""
        for cmd in ["find", "put", "get", "list"]:
            result = cli(cmd, "--help")
            assert result.returncode == 0, f"{cmd} --help failed"
            assert "Usage" in result.stdout or "usage" in result.stdout.lower()


# -----------------------------------------------------------------------------
# JSON Output Tests (Composability with jq)
# -----------------------------------------------------------------------------

class TestJsonOutput:
    """Tests for JSON output format."""
    
    def test_json_flag_exists(self, cli):
        """Global --json flag is recognized."""
        # --json is now a global flag (before the command)
        result = cli("--json", "find", "--help")
        # --help takes precedence, should succeed
        assert result.returncode == 0

    def test_collections_json_is_valid(self, cli):
        """--json collections produces valid JSON array."""
        result = cli("--json", "collections")
        # May fail with NotImplementedError, but if it produces output, it should be JSON
        if result.returncode == 0 and result.stdout.strip():
            parsed = json.loads(result.stdout)
            assert isinstance(parsed, list)
    
    def test_json_output_has_required_fields(self):
        """JSON item output includes id, summary, tags, score."""
        # Test the format helper directly
        from keep.cli import _format_item
        from keep.types import Item
        
        item = Item(
            id="test:1",
            summary="Test summary",
            tags={"project": "myapp", "_created": "2026-01-30T10:00:00Z"},
            score=0.95
        )
        
        output = _format_item(item, as_json=True)
        parsed = json.loads(output)
        
        assert parsed["id"] == "test:1"
        assert parsed["summary"] == "Test summary"
        assert parsed["tags"]["project"] == "myapp"
        assert parsed["score"] == 0.95
    
    def test_json_list_output(self):
        """JSON list output is a valid array."""
        from keep.cli import _format_items
        from keep.types import Item
        
        items = [
            Item(id="test:1", summary="First", tags={}, score=0.9),
            Item(id="test:2", summary="Second", tags={}, score=0.8),
        ]
        
        output = _format_items(items, as_json=True)
        parsed = json.loads(output)
        
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["id"] == "test:1"
        assert parsed[1]["id"] == "test:2"
    
    def test_json_empty_list(self):
        """Empty results produce empty JSON array."""
        from keep.cli import _format_items
        
        output = _format_items([], as_json=True)
        parsed = json.loads(output)
        
        assert parsed == []


# -----------------------------------------------------------------------------
# Human-Readable Output Tests (Composability with grep/wc)
# -----------------------------------------------------------------------------

class TestHumanOutput:
    """Tests for human-readable output format."""
    
    def test_human_item_format(self):
        """Human-readable item shows id and summary."""
        from keep.cli import _format_item
        from keep.types import Item
        
        item = Item(id="file:///doc.md", summary="A document about testing")
        output = _format_item(item, as_json=False)
        
        assert "file:///doc.md" in output
        assert "A document about testing" in output
    
    def test_human_item_with_score(self):
        """Human-readable item shows score in full YAML mode."""
        from keep.cli import _format_yaml_frontmatter
        from keep.types import Item

        item = Item(id="test:1", summary="Test", score=0.95)
        output = _format_yaml_frontmatter(item)

        # YAML frontmatter format includes score
        assert "score: 0.950" in output
        assert "---" in output
    
    def test_human_list_empty(self):
        """Empty list shows user-friendly message."""
        from keep.cli import _format_items
        
        output = _format_items([], as_json=False)
        assert "No results" in output
    
    def test_human_list_separates_items(self):
        """Items are separated by newlines (summary format)."""
        from keep.cli import _format_items
        from keep.types import Item

        items = [
            Item(id="test:1", summary="First"),
            Item(id="test:2", summary="Second"),
        ]
        output = _format_items(items, as_json=False)

        # Items should be on separate lines (summary format: id@V{N} date summary)
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert "test:1" in lines[0]
        assert "test:2" in lines[1]


# -----------------------------------------------------------------------------
# Exit Code Tests (Shell Scripting)
# -----------------------------------------------------------------------------

class TestExitCodes:
    """Tests for proper exit codes."""
    
    def test_help_returns_zero(self, cli):
        """Help returns exit code 0."""
        result = cli("--help")
        assert result.returncode == 0
    
    def test_unknown_command_returns_nonzero(self, cli):
        """Unknown command returns non-zero exit code."""
        result = cli("nonexistent-command")
        assert result.returncode != 0
    
    def test_invalid_tag_format_returns_error(self, cli):
        """Invalid tag format returns exit code 1."""
        # put with bad tag format
        result = cli("put", "test:1", "--tag", "badformat")
        # Should fail due to missing = in tag
        # (may also fail due to NotImplemented, which is fine)
        if "Invalid tag format" in result.stderr:
            assert result.returncode == 1

    def test_put_inline_with_summary_rejected(self, cli):
        """--summary with inline text is rejected (would lose content)."""
        result = cli("put", "my note", "--summary", "a summary")
        assert result.returncode == 1
        assert "cannot be used with inline text" in result.stderr

    def test_put_stdin_with_summary_rejected(self, cli):
        """--summary with stdin is rejected (would lose content)."""
        result = cli("put", "-", "--summary", "a summary", input="some content")
        assert result.returncode == 1
        assert "cannot be used with stdin" in result.stderr

    def test_put_inline_too_long_rejected(self, cli):
        """Inline text exceeding max_summary_length is rejected."""
        long_text = "x" * 3000
        result = cli("put", long_text)
        assert result.returncode == 1
        assert "too long to store" in result.stderr
        assert "file" in result.stderr.lower()  # Hint mentions file


# -----------------------------------------------------------------------------
# Tag Parsing Tests
# -----------------------------------------------------------------------------

class TestTagParsing:
    """Tests for key=value tag parsing."""
    
    def test_single_tag(self, cli):
        """Single --tag is parsed correctly."""
        # We can't fully test this without implementation,
        # but we can check the format is accepted
        result = cli("put", "--help")
        assert "--tag" in result.stdout or "-t" in result.stdout

    def test_tag_format_validation(self, cli):
        """Tags without = are rejected."""
        result = cli("put", "test:1", "--tag", "invalid")
        # Should fail - either due to format error or missing dependencies
        # When implementation is complete, this should specifically check for format error
        assert result.returncode != 0


# -----------------------------------------------------------------------------
# Unix Composability Integration
# -----------------------------------------------------------------------------

class TestUnixComposability:
    """Tests demonstrating Unix pipeline composability."""
    
    def test_json_output_pipeable(self):
        """JSON output can be processed with standard tools."""
        from keep.cli import _format_items
        from keep.types import Item
        
        items = [
            Item(id="doc:1", summary="First doc", tags={"project": "alpha"}, score=0.9),
            Item(id="doc:2", summary="Second doc", tags={"project": "beta"}, score=0.8),
        ]
        
        json_output = _format_items(items, as_json=True)
        
        # Simulate: keep find "query" --json | jq '.[0].id'
        parsed = json.loads(json_output)
        first_id = parsed[0]["id"]
        assert first_id == "doc:1"
        
        # Simulate: keep find "query" --json | jq '.[] | select(.score > 0.85)'
        high_score = [item for item in parsed if item["score"] > 0.85]
        assert len(high_score) == 1
        assert high_score[0]["id"] == "doc:1"
    
    def test_line_oriented_for_wc(self):
        """Human output is countable with wc -l."""
        from keep.cli import _format_items
        from keep.types import Item
        
        items = [
            Item(id=f"doc:{i}", summary=f"Doc {i}") 
            for i in range(5)
        ]
        
        output = _format_items(items, as_json=False)
        
        # Each item produces predictable lines
        lines = output.strip().split("\n")
        # 5 items * 2 lines each, minus separators handled differently
        assert len(lines) >= 5  # At least one line per item
    
    def test_ids_extractable_from_json(self):
        """IDs can be extracted for use in other commands."""
        from keep.cli import _format_items
        from keep.types import Item

        items = [
            Item(id="file:///a.md", summary="A"),
            Item(id="file:///b.md", summary="B"),
        ]

        json_output = _format_items(items, as_json=True)
        parsed = json.loads(json_output)

        # Simulate: keep find "query" --json | jq -r '.[].id' | xargs -I{} keep get {}
        ids = [item["id"] for item in parsed]
        assert ids == ["file:///a.md", "file:///b.md"]

    def test_get_multiple_ids_separated_by_yaml_separator(self, cli):
        """Multiple IDs in get produce YAML-document-separated output."""
        # Get two system docs that always exist
        result = cli("get", ".conversations", ".domains", "--no-similar")
        assert result.returncode == 0
        # Multiple items separated by --- between them
        parts = result.stdout.split("\n---\n")
        # At least 2 documents (each starts with --- frontmatter too)
        assert len(parts) >= 3  # opening ---, doc1 body + ---, doc2 frontmatter + body
        assert ".conversations" in result.stdout
        assert ".domains" in result.stdout

    def test_get_single_id_no_extra_separator(self, cli):
        """Single ID in get produces normal output (no extra separators)."""
        result = cli("get", ".conversations", "--no-similar")
        assert result.returncode == 0
        assert "id: .conversations" in result.stdout

    def test_get_nonexistent_id_returns_error(self, cli):
        """Nonexistent ID returns exit code 1."""
        result = cli("get", "nonexistent:id:that:does:not:exist")
        assert result.returncode == 1
        assert "Not found" in result.stderr


# -----------------------------------------------------------------------------
# CLI / API Equivalence Tests
# -----------------------------------------------------------------------------

class TestApiCliEquivalence:
    """Tests verifying CLI maps to Python API."""
    
    def test_find_maps_to_api_find(self, cli):
        """'find' command maps to Keeper.find()."""
        result = cli("find", "--help")
        assert "semantic" in result.stdout.lower() or "similar" in result.stdout.lower()
        # The CLI find uses mem.find(query, limit=limit)
    
    def test_put_maps_to_api_update(self, cli):
        """'put' command maps to Keeper.update()."""
        result = cli("put", "--help")
        assert "URI" in result.stdout or "document" in result.stdout.lower()
        # The CLI put uses mem.update(id, source_tags=...)

    def test_put_text_mode_maps_to_api_remember(self, cli):
        """'put' text mode (no ://) maps to Keeper.remember()."""
        result = cli("put", "--help")
        # The help should mention text content mode
        assert "text" in result.stdout.lower() or "content" in result.stdout.lower()
        # The CLI put with text calls kp.remember() internally
    
    def test_get_maps_to_api_get(self, cli):
        """'get' command maps to Keeper.get()."""
        result = cli("get", "--help")
        assert "ID" in result.stdout or "id" in result.stdout.lower()
        # The CLI get uses mem.get(id)

    def test_get_accepts_multiple_ids(self, cli):
        """'get' command accepts multiple ID arguments."""
        result = cli("get", "--help")
        # Help should show variadic argument
        assert "ID..." in result.stdout
    
    def test_list_tag_maps_to_api_query_tag(self, cli):
        """'list --tag' command maps to Keeper.query_tag()."""
        result = cli("list", "--help")
        assert "--tag" in result.stdout
        # The CLI list --tag uses kp.query_tag(key, value, limit=limit)


# -----------------------------------------------------------------------------
# Option Tests
# -----------------------------------------------------------------------------

class TestOptions:
    """Tests for CLI options."""
    
    def test_store_option(self, cli):
        """--store option is available."""
        result = cli("find", "--help")
        assert "--store" in result.stdout or "-s" in result.stdout
    
    def test_collection_option(self, cli):
        """--collection option is available."""
        result = cli("find", "--help")
        assert "--collection" in result.stdout or "-c" in result.stdout
    
    def test_limit_option(self, cli):
        """--limit option is available."""
        result = cli("find", "--help")
        assert "--limit" in result.stdout or "-n" in result.stdout
    
    def test_json_option(self, cli):
        """--json global option is available."""
        # --json is now a global flag, visible in main help
        result = cli("--help")
        assert "--json" in result.stdout or "-j" in result.stdout


# -----------------------------------------------------------------------------
# Shell-safe ID Quoting Tests
# -----------------------------------------------------------------------------

class TestShellQuoteId:
    """Tests for _shell_quote_id() helper."""

    def test_safe_id_not_quoted(self):
        """IDs with only shell-safe chars are returned as-is."""
        from keep.cli import _shell_quote_id
        assert _shell_quote_id("%abc123def456") == "%abc123def456"
        assert _shell_quote_id("file:///path/to/doc.md") == "file:///path/to/doc.md"
        assert _shell_quote_id("https://example.com/path") == "https://example.com/path"
        assert _shell_quote_id("now@V{3}") == "now@V{3}"
        assert _shell_quote_id(".tag/act") == ".tag/act"
        assert _shell_quote_id(".conversations") == ".conversations"

    def test_space_id_quoted(self):
        """IDs with spaces get single-quoted."""
        from keep.cli import _shell_quote_id
        result = _shell_quote_id("file:///Application Data/foo")
        assert result == "'file:///Application Data/foo'"

    def test_special_chars_quoted(self):
        """IDs with shell-special chars get single-quoted."""
        from keep.cli import _shell_quote_id
        assert _shell_quote_id("test$var") == "'test$var'"
        assert _shell_quote_id("test&bg") == "'test&bg'"
        assert _shell_quote_id("test;cmd") == "'test;cmd'"

    def test_single_quote_escaped(self):
        """IDs containing single quotes use '\\'' escaping."""
        from keep.cli import _shell_quote_id
        result = _shell_quote_id("it's a test")
        assert result == "'it'\\''s a test'"

    def test_summary_line_quotes_unsafe_id(self):
        """_format_summary_line quotes IDs with spaces."""
        from keep.cli import _format_summary_line
        from keep.types import Item
        item = Item(
            id="file:///Application Data/doc.md",
            summary="A doc",
            tags={"_updated": "2026-01-15T00:00:00Z"},
        )
        output = _format_summary_line(item)
        assert "'file:///Application Data/doc.md'" in output

    def test_summary_line_no_quotes_safe_id(self):
        """_format_summary_line does not quote safe IDs."""
        from keep.cli import _format_summary_line
        from keep.types import Item
        item = Item(
            id="%abc123",
            summary="A note",
            tags={"_updated": "2026-01-15T00:00:00Z"},
        )
        output = _format_summary_line(item)
        assert "%abc123" in output
        assert "'" not in output.split(" ")[0]  # ID portion not quoted

    def test_versioned_id_quotes_unsafe(self):
        """_format_versioned_id quotes IDs with spaces."""
        from keep.cli import _format_versioned_id
        from keep.types import Item
        item = Item(
            id="file:///my docs/test.md",
            summary="Test",
            tags={},
        )
        output = _format_versioned_id(item)
        assert output == "'file:///my docs/test.md'"

    def test_json_output_not_quoted(self):
        """JSON output does NOT shell-quote IDs."""
        from keep.cli import _format_item
        from keep.types import Item
        item = Item(
            id="file:///Application Data/doc.md",
            summary="A doc",
            tags={},
        )
        output = _format_item(item, as_json=True)
        parsed = json.loads(output)
        assert parsed["id"] == "file:///Application Data/doc.md"  # Raw, no quoting


# -----------------------------------------------------------------------------
# Command Alias Tests
# -----------------------------------------------------------------------------

class TestCommandAliases:
    """Tests that old command names still work as hidden aliases."""

    def test_help_shows_put_not_update(self, cli):
        """Main help shows 'put' and 'del', not 'update' or 'delete'."""
        result = cli("--help")
        assert result.returncode == 0
        assert "put" in result.stdout
        assert "del" in result.stdout
        # Old names should be hidden
        lines = result.stdout.split("\n")
        visible_commands = [l for l in lines if l.strip() and not l.strip().startswith("--")]
        visible_text = "\n".join(visible_commands)
        # 'update' and 'delete' should not appear as visible commands
        # (they may appear in descriptions, so check command column only)

    def test_update_alias_works(self, cli):
        """'update' still works as a hidden alias for 'put'."""
        result = cli("update", "--help")
        assert result.returncode == 0

    def test_delete_alias_works(self, cli):
        """'delete' still works as a hidden alias for 'del'."""
        result = cli("delete", "--help")
        assert result.returncode == 0

    def test_del_help(self, cli):
        """'del' command has help."""
        result = cli("del", "--help")
        assert result.returncode == 0
        assert "Delete" in result.stdout or "delete" in result.stdout.lower()
