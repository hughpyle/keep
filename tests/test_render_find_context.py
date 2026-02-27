# Copyright (c) 2026 Inguz Outcomes LLC.  All rights reserved.
"""Tests for render_find_context — token-budgeted prompt renderer."""

import pytest
from keep.types import Item, PromptResult


def _item(id="test", summary="Test summary", score=0.9, tags=None):
    return Item(id=id, summary=summary, score=score, tags=tags or {"_updated_date": "2026-02-20"})


class TestRenderFindContext:
    """Tests for the token-budgeted progressive renderer."""

    def test_basic_rendering(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="First item"), _item(id="b", summary="Second item")]
        result = render_find_context(items)
        assert "a" in result
        assert "First item" in result
        assert "b" in result
        assert "Second item" in result

    def test_empty_items(self):
        from keep.cli import render_find_context
        result = render_find_context([])
        assert result == "No results."

    def test_score_included(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="With score", score=0.85)]
        result = render_find_context(items)
        assert "(0.85)" in result

    def test_date_included(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="Dated", tags={"_updated_date": "2026-01-15"})]
        result = render_find_context(items)
        assert "2026-01-15" in result

    def test_focus_summary_rendered(self):
        """Focus summary replaces parent summary on the primary line."""
        from keep.cli import render_find_context
        items = [_item(id="a", summary="Parent doc",
                       tags={"_updated_date": "2026-02-20",
                             "_focus_summary": "The matching part content"})]
        result = render_find_context(items)
        assert "The matching part content" in result
        assert "Parent doc" not in result

    def test_budget_limits_items(self):
        """With a very small budget, only the first item should appear."""
        from keep.cli import render_find_context
        items = [
            _item(id="first", summary="A" * 200),
            _item(id="second", summary="B" * 200),
            _item(id="third", summary="C" * 200),
        ]
        # Each item line ~55 tokens. Budget of 30 should only fit first.
        result = render_find_context(items, token_budget=30)
        assert "first" in result
        # Second item should be cut off (budget exhausted by first)
        assert "second" not in result

    def test_large_budget_includes_all(self):
        """With a large budget, all items should appear."""
        from keep.cli import render_find_context
        items = [_item(id=f"item-{i}", summary=f"Summary {i}") for i in range(10)]
        result = render_find_context(items, token_budget=10000)
        for i in range(10):
            assert f"item-{i}" in result

    def test_no_score_when_none(self):
        from keep.cli import render_find_context
        items = [_item(id="a", summary="No score", score=None)]
        result = render_find_context(items)
        assert "(" not in result  # no score parens


class TestExpandPromptFindBudget:
    """Tests for {find:N} budget override syntax in expand_prompt."""

    def test_default_budget(self):
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Test")],
            prompt="Context:\n{find}\nEnd.",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "a" in output
        assert "{find}" not in output

    def test_budget_from_placeholder(self):
        """Budget specified in placeholder should override default."""
        from keep.cli import expand_prompt
        # Create many items
        items = [_item(id=f"item-{i}", summary="X" * 200) for i in range(20)]
        # Default budget is large, but placeholder says 50
        result = PromptResult(
            context=None,
            search_results=items,
            prompt="Context:\n{find:50}\nEnd.",
            token_budget=10000,
        )
        output = expand_prompt(result)
        # With budget=50 tokens, shouldn't fit all 20 items
        assert "item-0" in output
        assert "item-19" not in output

    def test_deep_with_budget(self):
        """The {find:deep:8000} syntax should be expanded."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Test")],
            prompt="{find:deep:8000}",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "a" in output
        assert "{find" not in output

    def test_deep_without_budget(self):
        """The {find:deep} syntax should use default budget."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=[_item(id="a", summary="Deep test")],
            prompt="{find:deep}",
            token_budget=4000,
        )
        output = expand_prompt(result)
        assert "Deep test" in output

    def test_no_results(self):
        """Empty search results should produce empty expansion."""
        from keep.cli import expand_prompt
        result = PromptResult(
            context=None,
            search_results=None,
            prompt="Before {find} After",
        )
        output = expand_prompt(result)
        assert "Before" in output
        assert "After" in output
        assert "{find}" not in output
