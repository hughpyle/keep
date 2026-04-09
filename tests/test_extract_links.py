"""Tests for the extract_links action and document-provider link extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock
from typing import Any

import pytest

from keep.actions.extract_links import (
    _parse_links,
    _resolve_internal_link,
    _detect_vault_root,
    ExtractLinks,
)
from keep.providers.documents import FileDocumentProvider
from keep.types import normalize_edge_value, parse_ref


# ---------------------------------------------------------------------------
# Link parsing
# ---------------------------------------------------------------------------

class TestParseLinks:
    """Tests for link parsing."""

    def test_wiki_link(self):
        links = _parse_links("See [[My Note]] for details.")
        assert len(links) == 1
        assert links[0]["target"] == "My Note"
        assert links[0]["style"] == "wiki"

    def test_wiki_link_with_display(self):
        links = _parse_links("See [[target|display text]] here.")
        assert len(links) == 1
        assert links[0]["target"] == "target"

    def test_markdown_link(self):
        links = _parse_links("Read [the docs](./docs/README.md).")
        assert len(links) == 1
        assert links[0]["target"] == "./docs/README.md"
        assert links[0]["style"] == "markdown"

    def test_markdown_url(self):
        links = _parse_links("See [example](https://example.com).")
        assert len(links) == 1
        assert links[0]["target"] == "https://example.com"

    def test_image_captured(self):
        links = _parse_links("![alt](image.png)")
        assert len(links) == 1
        assert links[0]["target"] == "image.png"
        assert links[0]["style"] == "markdown"

    def test_mixed_styles(self):
        content = "Link to [[wiki-note]] and [md](./other.md) and [url](https://x.com)"
        links = _parse_links(content)
        assert len(links) == 3
        targets = {l["target"] for l in links}
        assert targets == {"wiki-note", "./other.md", "https://x.com"}

    def test_dedup(self):
        links = _parse_links("[[foo]] and [[foo]] again")
        assert len(links) == 1

    def test_skip_anchors(self):
        links = _parse_links("[section](#heading)")
        assert len(links) == 0

    def test_skip_mailto(self):
        links = _parse_links("[email](mailto:x@y.com)")
        assert len(links) == 0

    def test_empty_content(self):
        assert _parse_links("") == []
        assert _parse_links("No links here.") == []


# ---------------------------------------------------------------------------
# Link resolution
# ---------------------------------------------------------------------------

class TestResolveInternalLink:
    """Tests for internal link resolution."""

    def _make_context(self, known_ids: set[str]):
        ctx = MagicMock()
        def _get(id):
            return MagicMock() if id in known_ids else None
        ctx.get = _get
        ctx.find_by_name = MagicMock(return_value=None)
        return ctx

    def test_direct_match(self):
        ctx = self._make_context({"my-note"})
        result = _resolve_internal_link("my-note", "source", ctx)
        assert result == "my-note"

    def test_file_uri_relative(self):
        ctx = self._make_context({"file:///vault/notes/other.md"})
        result = _resolve_internal_link(
            "other.md", "file:///vault/notes/current.md", ctx
        )
        assert result == "file:///vault/notes/other.md"

    def test_file_uri_without_extension(self):
        ctx = self._make_context({"file:///vault/notes/other.md"})
        result = _resolve_internal_link(
            "other", "file:///vault/notes/current.md", ctx
        )
        assert result == "file:///vault/notes/other.md"

    def test_file_uri_subdirectory(self):
        ctx = self._make_context({"file:///vault/notes/sub/deep.md"})
        result = _resolve_internal_link(
            "sub/deep.md", "file:///vault/notes/current.md", ctx
        )
        assert result == "file:///vault/notes/sub/deep.md"

    def test_not_found(self):
        ctx = self._make_context(set())
        result = _resolve_internal_link("missing", "file:///vault/x.md", ctx)
        assert result is None

    def test_bare_match_without_md(self):
        ctx = self._make_context({"CONTRIBUTING"})
        result = _resolve_internal_link("CONTRIBUTING.md", "source", ctx)
        assert result == "CONTRIBUTING"


# ---------------------------------------------------------------------------
# Action run
# ---------------------------------------------------------------------------

def _make_item(id: str, content: str, tags: dict | None = None):
    item = MagicMock()
    item.id = id
    item.summary = content[:100]
    item.content = content
    item.tags = tags or {}
    return item


def _make_context(items: dict[str, Any], item_id: str = "source.md"):
    ctx = MagicMock()
    ctx.item_id = item_id
    ctx.item_content = items.get(item_id, MagicMock()).content if item_id in items else ""

    def _get(id):
        return items.get(id)
    ctx.get = _get
    ctx.find_by_name = MagicMock(return_value=None)
    ctx.list_items = MagicMock(return_value=[])

    return ctx


class TestExtractLinksAction:
    """Tests for extract-links action."""

    def test_basic_wiki_link_resolved(self):
        source = _make_item("file:///vault/a.md", "See [[b]] for more.")
        target = _make_item("file:///vault/b.md", "Target content")
        ctx = _make_context(
            {"file:///vault/a.md": source, "file:///vault/b.md": target},
            item_id="file:///vault/a.md",
        )
        ctx.item_content = source.content

        result = ExtractLinks().run({"item_id": "file:///vault/a.md"}, ctx)

        assert not result.get("skipped")
        assert "[[file:///vault/b.md|b]]" in result["resolved"]
        # Should have set_tags mutation
        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        assert len(tag_mut) == 1
        assert "[[file:///vault/b.md|b]]" in tag_mut[0]["tags"]["references"]

    def test_wiki_link_with_display_preserves_display(self):
        source = _make_item("file:///vault/a.md", "See [[b|Bee note]] for more.")
        target = _make_item("file:///vault/b.md", "Target content")
        ctx = _make_context(
            {"file:///vault/a.md": source, "file:///vault/b.md": target},
            item_id="file:///vault/a.md",
        )
        ctx.item_content = source.content

        result = ExtractLinks().run({"item_id": "file:///vault/a.md"}, ctx)

        assert "[[file:///vault/b.md|Bee note]]" in result["resolved"]

    def test_external_url(self):
        source = _make_item("file:///vault/a.md", "See [docs](https://example.com).")
        ctx = _make_context(
            {"file:///vault/a.md": source},
            item_id="file:///vault/a.md",
        )
        ctx.item_content = source.content

        result = ExtractLinks().run({"item_id": "file:///vault/a.md"}, ctx)

        assert "https://example.com" in result["resolved"]
        # Should have put_item for auto-vivification + set_tags
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"]
        assert len(put_muts) == 1
        assert put_muts[0]["id"] == "https://example.com"

    def test_doc_links_string_form(self):
        """Legacy string-form doc_links still merge as bare URLs."""
        source = _make_item("a.html", "Plain HTML body, no parseable links.")
        ctx = _make_context({"a.html": source}, item_id="a.html")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {"item_id": "a.html", "doc_links": ["https://example.com"]}, ctx,
        )

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        assert tag_mut[0]["tags"]["references"] == ["https://example.com"]
        # Untitled URL → put_item still fires for background summarization.
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"]
        assert any(m["id"] == "https://example.com" for m in put_muts)

    def test_doc_links_dict_with_title(self):
        """Dict-form doc_links with title encode the alias and skip put_item."""
        source = _make_item("a.html", "Plain HTML body, no parseable links.")
        ctx = _make_context({"a.html": source}, item_id="a.html")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {
                "item_id": "a.html",
                "doc_links": [
                    {"url": "https://example.com", "title": "Example Site"},
                ],
            },
            ctx,
        )

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        refs = tag_mut[0]["tags"]["references"]
        assert "[[https://example.com|Example Site]]" in refs
        # Titled URL → no put_item; edge processor will auto-vivify with name.
        put_muts = [
            m for m in result["mutations"]
            if m["op"] == "put_item" and m["id"] == "https://example.com"
        ]
        assert put_muts == []

    def test_doc_links_dict_without_title(self):
        """Dict-form doc_links without title behave like the legacy string form."""
        source = _make_item("a.html", "Plain HTML body, no parseable links.")
        ctx = _make_context({"a.html": source}, item_id="a.html")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {"item_id": "a.html", "doc_links": [{"url": "https://example.com"}]},
            ctx,
        )

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        assert tag_mut[0]["tags"]["references"] == ["https://example.com"]
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"]
        assert any(m["id"] == "https://example.com" for m in put_muts)

    def test_no_links_skipped(self):
        source = _make_item("a.md", "No links here.")
        ctx = _make_context({"a.md": source}, item_id="a.md")
        ctx.item_content = source.content
        result = ExtractLinks().run({"item_id": "a.md"}, ctx)
        assert result.get("skipped") is True

    def test_custom_tag_key(self):
        source = _make_item("a.md", "See [[b]].")
        target = _make_item("b", "Target")
        ctx = _make_context({"a.md": source, "b": target}, item_id="a.md")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {"item_id": "a.md", "tag": "links_to"}, ctx
        )

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        assert "links_to" in tag_mut[0]["tags"]

    def test_create_targets_false(self):
        source = _make_item("a.md", "See [x](https://missing.com).")
        ctx = _make_context({"a.md": source}, item_id="a.md")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {"item_id": "a.md", "create_targets": "false"}, ctx
        )

        # URL still resolves (external URLs are always accepted)
        assert "https://missing.com" in result["resolved"]
        # But no put_item mutation
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"]
        assert len(put_muts) == 0

    def test_create_targets_false_strips_titles(self):
        """Titled URLs with create_targets=false drop the alias.

        The caller explicitly asked not to create target docs. The
        edge processor's auto-vivify already creates the stub
        (existing behavior for URL tag values), but stripping the
        title prevents the name-seeding path that would otherwise
        upgrade the stub to a named entity.
        """
        source = _make_item("a.html", "Plain HTML body, no parseable links.")
        ctx = _make_context({"a.html": source}, item_id="a.html")
        ctx.item_content = source.content

        result = ExtractLinks().run(
            {
                "item_id": "a.html",
                "create_targets": "false",
                "doc_links": [
                    {"url": "https://example.com", "title": "Example Site"},
                ],
            },
            ctx,
        )

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        refs = tag_mut[0]["tags"]["references"]
        # Alias is stripped — bare URL only.
        assert "https://example.com" in refs
        assert "[[https://example.com|Example Site]]" not in refs
        # No put_item either.
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"]
        assert put_muts == []

    def test_auto_vivify_internal(self):
        source = _make_item("file:///vault/a.md", "See [[missing-note]].")
        ctx = _make_context(
            {"file:///vault/a.md": source},
            item_id="file:///vault/a.md",
        )
        ctx.item_content = source.content

        result = ExtractLinks().run({"item_id": "file:///vault/a.md"}, ctx)

        assert not result.get("skipped")
        put_muts = [m for m in result["mutations"] if m["op"] == "put_item"
                     and m["id"] != ".vault/None"]  # exclude vault registration
        assert len(put_muts) == 1
        assert put_muts[0]["id"] == "file:///vault/missing-note.md"
        assert put_muts[0]["tags"]["_link_stem"] == "missing-note"

    def test_merges_with_existing_references(self):
        source = _make_item(
            "a.md", "See [[new-link]].",
            tags={"references": ["existing-ref"]},
        )
        target = _make_item("new-link", "Target")
        ctx = _make_context(
            {"a.md": source, "new-link": target}, item_id="a.md",
        )
        ctx.item_content = source.content

        result = ExtractLinks().run({"item_id": "a.md"}, ctx)

        tag_mut = [m for m in result["mutations"] if m["op"] == "set_tags"]
        refs = tag_mut[0]["tags"]["references"]
        assert "existing-ref" in refs
        assert "[[new-link|new-link]]" in refs


# ---------------------------------------------------------------------------
# parse_ref
# ---------------------------------------------------------------------------

class TestParseRef:
    """Tests for reference parsing."""

    def test_plain_id(self):
        assert parse_ref("file:///vault/Foo.md") == ("file:///vault/Foo.md", None)

    def test_with_alias_legacy(self):
        assert parse_ref("file:///vault/Foo.md[[Foo]]") == ("file:///vault/Foo.md", "Foo")

    def test_with_alias_canonical(self):
        assert parse_ref("[[file:///vault/Foo.md|Foo]]") == ("file:///vault/Foo.md", "Foo")

    def test_url_no_alias(self):
        assert parse_ref("https://example.com") == ("https://example.com", None)

    def test_empty_alias(self):
        assert parse_ref("id[[]]") == ("id", "")

    def test_empty_alias_canonical(self):
        assert parse_ref("[[id|]]") == ("id", "")

    def test_canonical_unlabeled_ref(self):
        assert parse_ref("[[id]]") == ("id", None)

    def test_no_closing_brackets(self):
        assert parse_ref("id[[Foo") == ("id[[Foo", None)

    def test_nested_brackets(self):
        # [[...]] should find the last [[
        assert parse_ref("a[[b]]c[[d]]") == ("a[[b]]c", "d")


# ---------------------------------------------------------------------------
# normalize_edge_value
# ---------------------------------------------------------------------------

class TestNormalizeEdgeValue:
    """Tests for canonical labeled-ref normalization on edge values."""

    def test_https_link(self):
        assert (
            normalize_edge_value("[Example](https://example.com)")
            == "[[https://example.com|Example]]"
        )

    def test_http_link(self):
        assert (
            normalize_edge_value("[Example](http://example.com)")
            == "[[http://example.com|Example]]"
        )

    def test_file_link(self):
        assert (
            normalize_edge_value("[Doc](file:///vault/notes/Doc.md)")
            == "[[file:///vault/notes/Doc.md|Doc]]"
        )

    def test_title_with_spaces(self):
        assert (
            normalize_edge_value("[A Long Title](https://example.com/page)")
            == "[[https://example.com/page|A Long Title]]"
        )

    def test_title_strips_whitespace(self):
        assert (
            normalize_edge_value("  [Example](https://example.com)  ")
            == "[[https://example.com|Example]]"
        )

    def test_already_canonical_legacy_is_rewritten(self):
        assert (
            normalize_edge_value("https://example.com[[Example]]")
            == "[[https://example.com|Example]]"
        )

    def test_already_canonical_new_unchanged(self):
        assert (
            normalize_edge_value("[[https://example.com|Example]]")
            == "[[https://example.com|Example]]"
        )

    def test_bare_url_unchanged(self):
        assert normalize_edge_value("https://example.com") == "https://example.com"

    def test_internal_id_unchanged(self):
        assert normalize_edge_value("file:///vault/Foo.md") == "file:///vault/Foo.md"

    def test_relative_markdown_unchanged(self):
        # Relative paths are extract_links territory, not edge tag values.
        assert normalize_edge_value("[Doc](./other.md)") == "[Doc](./other.md)"

    def test_mailto_unchanged(self):
        assert (
            normalize_edge_value("[Email](mailto:x@y.com)")
            == "[Email](mailto:x@y.com)"
        )

    def test_nested_brackets_in_title_unchanged(self):
        # Strict regex: titles containing ] fall through. Acceptable.
        assert (
            normalize_edge_value("[Foo [bar]](https://example.com)")
            == "[Foo [bar]](https://example.com)"
        )

    def test_unsafe_close_bracket_pair_unchanged(self):
        # Title containing ]] would break labeled-ref re-parsing.
        # Note: ]] in title also breaks the markdown match itself, so this
        # path is doubly defended.
        assert (
            normalize_edge_value("[bad]]title](https://example.com)")
            == "[bad]]title](https://example.com)"
        )

    def test_embedded_link_in_text_unchanged(self):
        # Only whole-string matches normalize. Embedded markdown is content.
        v = "see [Example](https://example.com) for more"
        assert normalize_edge_value(v) == v

    def test_empty_string(self):
        assert normalize_edge_value("") == ""

    def test_no_brackets_short_circuit(self):
        assert normalize_edge_value("plain text value") == "plain text value"


# ---------------------------------------------------------------------------
# Vault-wide wiki resolution
# ---------------------------------------------------------------------------

class TestVaultWideResolution:
    """Tests for vault-wide link resolution."""

    def _make_context(self, known_ids: set[str], find_by_name_result=None):
        ctx = MagicMock()
        def _get(id):
            return MagicMock() if id in known_ids else None
        ctx.get = _get
        ctx.find_by_name = MagicMock(return_value=find_by_name_result)
        return ctx

    def test_wiki_vault_wide_fallback(self):
        """Wiki link that fails folder-relative falls back to find_by_name."""
        found = MagicMock()
        found.id = "file:///vault/deep/nested/Bar.md"
        ctx = self._make_context(set(), find_by_name_result=found)

        result = _resolve_internal_link(
            "Bar", "file:///vault/notes/source.md", ctx,
            style="wiki", vault_root="file:///vault",
        )
        assert result == "file:///vault/deep/nested/Bar.md"
        ctx.find_by_name.assert_called_once_with("Bar", vault="file:///vault")

    def test_markdown_no_vault_fallback(self):
        """Markdown links do NOT use vault-wide fallback."""
        ctx = self._make_context(set())

        result = _resolve_internal_link(
            "Bar", "file:///vault/notes/source.md", ctx,
            style="markdown",
        )
        assert result is None
        ctx.find_by_name.assert_not_called()

    def test_wiki_folder_relative_preferred(self):
        """Folder-relative match is preferred over vault-wide."""
        ctx = self._make_context({"file:///vault/notes/Bar.md"})

        result = _resolve_internal_link(
            "Bar", "file:///vault/notes/source.md", ctx,
            style="wiki", vault_root="file:///vault",
        )
        assert result == "file:///vault/notes/Bar.md"
        ctx.find_by_name.assert_not_called()

    def test_wiki_not_found_anywhere(self):
        """Wiki link not found folder-relative or vault-wide returns None."""
        ctx = self._make_context(set(), find_by_name_result=None)

        result = _resolve_internal_link(
            "Missing", "file:///vault/notes/source.md", ctx,
            style="wiki", vault_root="file:///vault",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Document provider: HTML link extraction
# ---------------------------------------------------------------------------

class TestExtractHtmlLinks:
    """Tests for FileDocumentProvider._extract_html_links."""

    def _write(self, tmp_path: Path, html: str) -> Path:
        path = tmp_path / "page.html"
        path.write_text(html, encoding="utf-8")
        return path

    def test_anchor_with_text(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body><a href="https://example.com">Example Site</a></body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com", "title": "Example Site"}]

    def test_anchor_without_text(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body><a href="https://example.com"></a></body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com"}]

    def test_anchor_text_equals_url(self, tmp_path):
        # No point storing the URL as its own title.
        path = self._write(
            tmp_path,
            '<html><body><a href="https://example.com">https://example.com</a></body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com"}]

    def test_unsafe_title_rejected(self, tmp_path):
        # Titles containing ]] would break the labeled-ref encoding.
        path = self._write(
            tmp_path,
            '<html><body><a href="https://example.com">bad]]title</a></body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com"}]

    def test_skip_non_http(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body>'
            '<a href="mailto:x@y.com">mail</a>'
            '<a href="#anchor">anchor</a>'
            '<a href="/relative">rel</a>'
            '</body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links is None

    def test_dedup_first_wins(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body>'
            '<a href="https://example.com">First</a>'
            '<a href="https://example.com">Second</a>'
            '</body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com", "title": "First"}]

    def test_nested_text(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body><a href="https://example.com">'
            '<span>The </span><b>Example</b> Site</a></body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [{"url": "https://example.com", "title": "The Example Site"}]

    def test_multiple_links(self, tmp_path):
        path = self._write(
            tmp_path,
            '<html><body>'
            '<a href="https://a.com">A</a>'
            '<a href="https://b.com">B</a>'
            '</body></html>',
        )
        links = FileDocumentProvider._extract_html_links(path)
        assert links == [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
        ]
