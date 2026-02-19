"""Unit tests for sliding-window analyzer internals."""

import pytest
from unittest.mock import patch

from keep.analyzers import (
    _estimate_tokens,
    _parse_parts,
    PROMPTS,
    DEFAULT_PROMPT,
    SlidingWindowAnalyzer,
)
from keep.providers.base import AnalysisChunk


class TestEstimateTokens:

    def test_basic(self):
        assert _estimate_tokens("abcd") == 1
        assert _estimate_tokens("a" * 400) == 100

    def test_empty(self):
        assert _estimate_tokens("") == 0


class TestParseParts:

    def test_clean_lines(self):
        text = "First significant observation about the code.\nSecond observation about the architecture."
        result = _parse_parts(text)
        assert len(result) == 2
        assert result[0]["summary"] == "First significant observation about the code."
        assert result[1]["summary"] == "Second observation about the architecture."

    def test_strips_preamble(self):
        text = "Here are the significant developments:\nActual observation about the change."
        result = _parse_parts(text)
        assert len(result) == 1
        assert result[0]["summary"] == "Actual observation about the change."

    def test_strips_xml_leaks(self):
        text = "<analyze>Decision made to use sliding windows</analyze>"
        result = _parse_parts(text)
        assert len(result) == 1
        assert "<analyze>" not in result[0]["summary"]
        assert "</analyze>" not in result[0]["summary"]

    def test_filters_empty_sentinel(self):
        text = "EMPTY"
        result = _parse_parts(text)
        assert result == []

    def test_filters_short_lines(self):
        text = "ok\ntoo short\nThis is a long enough observation about the change."
        result = _parse_parts(text)
        assert len(result) == 1
        assert "long enough" in result[0]["summary"]

    def test_empty_input(self):
        assert _parse_parts("") == []
        assert _parse_parts(None) == []

    def test_strips_summary_preamble(self):
        text = "Here is a summary of the document: The actual content is about authentication patterns."
        result = _parse_parts(text)
        assert len(result) == 1
        # strip_summary_preamble should have removed "Here is a summary..."
        assert "Here is a summary" not in result[0]["summary"]


class TestPrompts:

    def test_all_expected_keys(self):
        expected = {"structural", "temporal", "temporal-v2", "temporal-v3", "temporal-v4", "commitments"}
        assert set(PROMPTS.keys()) == expected

    def test_default_prompt_exists(self):
        assert DEFAULT_PROMPT in PROMPTS

    def test_prompts_are_nonempty_strings(self):
        for key, prompt in PROMPTS.items():
            assert isinstance(prompt, str), f"PROMPTS[{key!r}] is not a string"
            assert len(prompt) > 50, f"PROMPTS[{key!r}] seems too short"


class TestSlidingWindowAnalyzer:

    def test_unknown_prompt_raises(self):
        with pytest.raises(ValueError, match="Unknown prompt"):
            SlidingWindowAnalyzer(prompt="nonexistent")

    def test_build_window_prompt_single_target(self):
        chunks = [
            AnalysisChunk(content="Before context", tags={}, index=0),
            AnalysisChunk(content="Target content", tags={}, index=1),
            AnalysisChunk(content="After context", tags={}, index=2),
        ]
        prompt = SlidingWindowAnalyzer._build_window_prompt(chunks, 1, 2)
        assert "<content>" in prompt
        assert "</content>" in prompt
        assert "<analyze>" in prompt
        assert "</analyze>" in prompt
        # Target content should be between analyze tags
        analyze_start = prompt.index("<analyze>")
        analyze_end = prompt.index("</analyze>")
        assert "Target content" in prompt[analyze_start:analyze_end]
        # Context should be outside analyze tags
        assert prompt.index("Before context") < analyze_start
        assert prompt.index("After context") > analyze_end

    def test_build_window_prompt_multiple_targets(self):
        chunks = [
            AnalysisChunk(content="Chunk A", tags={}, index=0),
            AnalysisChunk(content="Chunk B", tags={}, index=1),
            AnalysisChunk(content="Chunk C", tags={}, index=2),
        ]
        # Target is chunks 0-2 (all of them)
        prompt = SlidingWindowAnalyzer._build_window_prompt(chunks, 0, 3)
        assert "<analyze>" in prompt
        assert "</analyze>" in prompt
        # All chunks should be between analyze tags
        analyze_start = prompt.index("<analyze>")
        analyze_end = prompt.index("</analyze>")
        for name in ("Chunk A", "Chunk B", "Chunk C"):
            assert name in prompt[analyze_start:analyze_end]

    def test_empty_chunks_returns_empty(self):
        analyzer = SlidingWindowAnalyzer()
        result = analyzer.analyze([])
        assert result == []

    def test_no_provider_returns_empty(self):
        analyzer = SlidingWindowAnalyzer(provider=None)
        chunks = [AnalysisChunk(content="Some content here", tags={}, index=0)]
        result = analyzer.analyze(chunks)
        assert result == []

    def test_dedup_across_windows(self):
        """Verify deduplication removes identical summaries across windows."""
        analyzer = SlidingWindowAnalyzer(context_budget=50, target_ratio=0.5)

        # Create chunks that exceed the budget (each ~125 tokens, budget is 50)
        chunks = [
            AnalysisChunk(content="x" * 500, tags={}, index=0),
            AnalysisChunk(content="y" * 500, tags={}, index=1),
        ]

        # Mock _analyze_window to return overlapping results
        with patch.object(analyzer, "_analyze_window") as mock_window:
            mock_window.side_effect = [
                [{"summary": "Duplicate observation"}, {"summary": "Unique to window 1"}],
                [{"summary": "Duplicate observation"}, {"summary": "Unique to window 2"}],
            ]
            result = analyzer.analyze(chunks)

        # Should have 3 parts (duplicate removed)
        assert len(result) == 3
        summaries = [p["summary"] for p in result]
        assert summaries.count("Duplicate observation") == 1
