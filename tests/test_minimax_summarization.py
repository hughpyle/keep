"""Unit tests for the MiniMax summarization provider.

MiniMax exposes an OpenAI-compatible /v1/chat/completions surface at
api.minimax.io.  ``MiniMaxSummarization`` is a thin subclass of
``OpenAISummarization`` that pins the base URL and resolves the API key
from ``MINIMAX_API_KEY``.  These tests confirm:

  * The MiniMax-specific env var is honoured.
  * Auth fails fast when no key is set.
  * Arbitrary model names are accepted (configurable via keep.toml).
  * The base URL is pinned to MiniMax's gateway, not OpenAI's default.
"""

from unittest.mock import patch

import pytest

from keep.providers.llm import MiniMaxSummarization


@pytest.fixture
def minimax_key(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    # Make sure we don't accidentally fall back to OpenAI's env vars.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)


class TestConstruction:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
            MiniMaxSummarization()

    def test_default_model(self, minimax_key):
        with patch("keep.providers.llm.create_openai_client") as mock_create:
            s = MiniMaxSummarization()
            assert s.model_name == "MiniMax-M2.7"
            mock_create.assert_called_once()
            kwargs = mock_create.call_args.kwargs
            assert kwargs["base_url"] == "https://api.minimax.io/v1"
            assert kwargs["api_key"] == "test-minimax-key"

    def test_alternative_model_passed_through(self, minimax_key):
        """Users must be able to override the model via keep.toml."""
        with patch("keep.providers.llm.create_openai_client"):
            for model in (
                "MiniMax-M2.7-highspeed",
                "MiniMax-M2.5",
                "MiniMax-M2.1",
                "MiniMax-Future",
            ):
                s = MiniMaxSummarization(model=model)
                assert s.model_name == model

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with patch("keep.providers.llm.create_openai_client") as mock_create:
            MiniMaxSummarization(api_key="explicit-key")
            assert mock_create.call_args.kwargs["api_key"] == "explicit-key"

    def test_does_not_use_openai_new_api_branch(self, minimax_key):
        """MiniMax model names must not trigger OpenAI's GPT-5/o3 code path.

        ``OpenAISummarization`` switches to ``max_completion_tokens`` for
        OpenAI reasoning models — that branch is gated on ``not base_url``
        so MiniMax should always use the standard ``max_tokens`` shape.
        """
        with patch("keep.providers.llm.create_openai_client"):
            s = MiniMaxSummarization()
            assert s._new_api is False
            kwargs = s._completion_kwargs(100)
            assert "max_tokens" in kwargs
            assert "max_completion_tokens" not in kwargs


class TestThinkBlockStripping:
    """MiniMax-M2.x are reasoning models that leak <think> chains into content."""

    def test_strips_think_block(self, minimax_key):
        with patch("keep.providers.llm.create_openai_client"):
            s = MiniMaxSummarization()
            with patch.object(
                s.__class__.__mro__[1],  # OpenAISummarization.generate
                "generate",
                return_value=(
                    "<think>\nLet me reason about this carefully.\n"
                    "Step 1: identify facts.\nStep 2: summarize.\n</think>\n\n"
                    "The Apollo program ran from 1961-1972."
                ),
            ):
                out = s.generate("sys", "user")
        assert out == "The Apollo program ran from 1961-1972."
        assert "<think>" not in out

    def test_strips_multiple_think_blocks(self, minimax_key):
        with patch("keep.providers.llm.create_openai_client"):
            s = MiniMaxSummarization()
            with patch.object(
                s.__class__.__mro__[1],
                "generate",
                return_value="<think>first</think>Real answer.<think>second</think>",
            ):
                out = s.generate("sys", "user")
        assert "<think>" not in out
        assert "Real answer." in out

    def test_returns_none_when_only_think_block(self, minimax_key):
        """If the model only emitted reasoning and ran out of budget."""
        with patch("keep.providers.llm.create_openai_client"):
            s = MiniMaxSummarization()
            with patch.object(
                s.__class__.__mro__[1],
                "generate",
                return_value="<think>thought but no answer</think>",
            ):
                out = s.generate("sys", "user")
        assert out is None

    def test_passes_through_non_reasoning_content(self, minimax_key):
        with patch("keep.providers.llm.create_openai_client"):
            s = MiniMaxSummarization()
            with patch.object(
                s.__class__.__mro__[1],
                "generate",
                return_value="Plain summary with no reasoning block.",
            ):
                out = s.generate("sys", "user")
        assert out == "Plain summary with no reasoning block."


class TestRegistration:
    def test_registered_in_global_registry(self):
        from keep.providers.base import get_registry
        assert "minimax" in get_registry().list_summarization_providers()
