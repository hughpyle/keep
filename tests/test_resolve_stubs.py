"""Tests for the resolve_stubs action."""

from __future__ import annotations

from unittest.mock import MagicMock
from typing import Any

from keep.actions.resolve_stubs import ResolveStubs, _file_stem


class TestFileStem:

    def test_md_file(self):
        assert _file_stem("file:///vault/notes/Foo.md") == "Foo"

    def test_no_extension(self):
        assert _file_stem("file:///vault/notes/Foo") == "Foo"

    def test_nested(self):
        assert _file_stem("file:///vault/deep/nested/Bar.md") == "Bar"

    def test_not_file_uri(self):
        assert _file_stem("https://example.com") is None

    def test_spaces(self):
        assert _file_stem("file:///vault/My Note.md") == "My Note"


def _make_item(id: str, summary: str = "", tags: dict | None = None):
    item = MagicMock()
    item.id = id
    item.summary = summary
    item.tags = tags or {}
    return item


def _make_context(
    items: dict[str, Any],
    *,
    list_results: list | None = None,
    referrers: list | None = None,
):
    ctx = MagicMock()

    def _get(id):
        return items.get(id)
    ctx.get = _get

    ctx.list_items = MagicMock(return_value=list_results or [])
    ctx.find_referencing = MagicMock(return_value=referrers or [])
    ctx.find_by_name = MagicMock(return_value=None)

    return ctx


class TestResolveStubs:

    def test_skips_non_file(self):
        ctx = _make_context({})
        result = ResolveStubs().run({"item_id": "https://example.com"}, ctx)
        assert result["skipped"] is True

    def test_skips_no_stubs(self):
        ctx = _make_context({}, list_results=[])
        result = ResolveStubs().run(
            {"item_id": "file:///vault/Foo.md"}, ctx,
        )
        assert result["skipped"] is True

    def test_rewrites_reference(self):
        stub = _make_item(
            "file:///vault/Foo.md",
            tags={"_source": "link", "_link_stem": "Foo"},
        )
        # The real note arrives at a different path
        real_id = "file:///vault/notes/Foo.md"

        # Source item that references the stub
        source = _make_item(
            "file:///vault/Source.md",
            tags={"references": ["file:///vault/Foo.md[[Foo]]"]},
        )

        ctx = _make_context(
            {"file:///vault/Foo.md": stub, real_id: _make_item(real_id)},
            list_results=[stub],
            referrers=[source],
        )

        result = ResolveStubs().run({"item_id": real_id}, ctx)

        assert not result.get("skipped")
        assert result["references_rewritten"] == 1

        # Check the mutation rewrites stub → real
        set_tags_muts = [m for m in result["mutations"] if m["op"] == "set_tags"]
        assert len(set_tags_muts) == 1
        refs = set_tags_muts[0]["tags"]["references"]
        assert f"{real_id}[[Foo]]" in refs
        assert "file:///vault/Foo.md[[Foo]]" not in refs

    def test_preserves_other_references(self):
        stub = _make_item(
            "file:///vault/Foo.md",
            tags={"_source": "link", "_link_stem": "Foo"},
        )
        real_id = "file:///vault/notes/Foo.md"

        source = _make_item(
            "file:///vault/Source.md",
            tags={"references": [
                "file:///vault/Foo.md[[Foo]]",
                "file:///vault/Other.md[[Other]]",
            ]},
        )

        ctx = _make_context(
            {real_id: _make_item(real_id)},
            list_results=[stub],
            referrers=[source],
        )

        result = ResolveStubs().run({"item_id": real_id}, ctx)

        refs = result["mutations"][0]["tags"]["references"]
        assert f"{real_id}[[Foo]]" in refs
        assert "file:///vault/Other.md[[Other]]" in refs

    def test_skips_self_stub(self):
        """If the stub ID is the same as the new item, skip it."""
        stub = _make_item(
            "file:///vault/Foo.md",
            tags={"_source": "link", "_link_stem": "Foo"},
        )
        ctx = _make_context(
            {},
            list_results=[stub],
        )
        result = ResolveStubs().run(
            {"item_id": "file:///vault/Foo.md"}, ctx,
        )
        assert result["skipped"] is True
