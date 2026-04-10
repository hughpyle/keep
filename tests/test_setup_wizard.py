"""Tests for the first-run setup wizard."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from keep.setup_wizard import (
    needs_wizard,
    detect_embedding_choices,
    detect_summarization_choices,
    detect_tool_choices,
    run_wizard,
)


class TestNeedsWizard:
    """Tests for wizard-needed detection."""
    def test_needs_wizard_no_config(self, tmp_path):
        assert needs_wizard(tmp_path) is True

    def test_needs_wizard_with_config(self, tmp_path):
        (tmp_path / "keep.toml").write_text("[store]\nversion = 3\n")
        assert needs_wizard(tmp_path) is False


class TestDetectToolChoices:
    """Tests for tool choice detection."""
    def test_detects_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".kiro").mkdir()

        choices = detect_tool_choices()
        found = {c["key"]: c["found"] for c in choices}
        assert found["claude_code"] is True
        assert found["kiro"] is True
        assert found["codex"] is False
        assert found["openclaw"] is False

    def test_no_tools_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        choices = detect_tool_choices()
        assert all(not c["found"] for c in choices)


class TestDetectEmbeddingChoices:
    """Tests for embedding choice detection."""
    def test_ollama_available(self, monkeypatch):
        monkeypatch.setattr(
            "keep.setup_wizard._detect_ollama",
            lambda: {"base_url": "http://localhost:11434", "models": ["nomic-embed-text"]},
        )
        # Suppress API key detection
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        choices = detect_embedding_choices()
        ollama_choices = [c for c in choices if "Ollama" in c["name"]]
        assert len(ollama_choices) == 1
        assert ollama_choices[0]["available"] is True
        assert ollama_choices[0]["default"] is True

    def test_no_ollama_api_key_default(self, monkeypatch):
        monkeypatch.setattr("keep.setup_wizard._detect_ollama", lambda: None)
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        choices = detect_embedding_choices()
        voyage = [c for c in choices if "Voyage" in c["name"]]
        assert len(voyage) == 1
        assert voyage[0]["available"] is True
        assert voyage[0]["default"] is True

    def test_unavailable_shows_requirement(self, monkeypatch):
        monkeypatch.setattr("keep.setup_wizard._detect_ollama", lambda: None)
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        choices = detect_embedding_choices()
        openai = [c for c in choices if "OpenAI" in c["name"]]
        assert len(openai) == 1
        assert openai[0]["available"] is False
        assert "requires" in openai[0]["hint"]

    def test_openrouter_shown_only_when_key_present(self, monkeypatch):
        monkeypatch.setattr("keep.setup_wizard._detect_ollama", lambda: None)
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        choices = detect_embedding_choices()
        assert not any("OpenRouter" in c["name"] for c in choices)

        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        choices = detect_embedding_choices()
        openrouter = [c for c in choices if "OpenRouter" in c["name"]]
        assert len(openrouter) == 1
        assert openrouter[0]["available"] is True
        assert openrouter[0]["value"] == ("openrouter", {"model": "openai/text-embedding-3-small"})


class TestDetectSummarizationChoices:
    """Tests for summarization choice detection."""
    def test_always_has_truncate_fallback(self, monkeypatch):
        monkeypatch.setattr("keep.setup_wizard._detect_ollama", lambda: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        choices = detect_summarization_choices()
        truncate = [c for c in choices if "truncate" in c["name"]]
        assert len(truncate) == 1
        assert truncate[0]["available"] is True

    def test_openrouter_summarization_shown_only_when_key_present(self, monkeypatch):
        monkeypatch.setattr("keep.setup_wizard._detect_ollama", lambda: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KEEP_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        choices = detect_summarization_choices()
        assert not any("OpenRouter" in c["name"] for c in choices)

        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        choices = detect_summarization_choices()
        openrouter = [c for c in choices if "OpenRouter" in c["name"]]
        assert len(openrouter) == 1
        assert openrouter[0]["available"] is True
        assert openrouter[0]["value"] == ("openrouter", {"model": "openai/gpt-4.1-mini"})


class TestRunWizardNonInteractive:
    """Tests for non-interactive wizard fallback."""
    def test_non_interactive_fallback(self, tmp_path, monkeypatch, mock_providers):
        """Non-interactive mode creates config without installing integrations."""
        monkeypatch.setattr("keep.setup_wizard._is_interactive", lambda: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with patch("keep.integrations.check_and_install", side_effect=AssertionError("should not be called")):
            config = run_wizard(tmp_path)
        assert config is not None
        assert (tmp_path / "keep.toml").exists()
