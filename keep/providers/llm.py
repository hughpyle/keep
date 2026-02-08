"""
Summarization and tagging providers using LLMs.
"""

import json
import os
from typing import Any

from .base import (
    SummarizationProvider,
    TaggingProvider,
    get_registry,
    SUMMARIZATION_SYSTEM_PROMPT,
    build_summarization_prompt,
    strip_summary_preamble,
)


# -----------------------------------------------------------------------------
# Summarization Providers
# -----------------------------------------------------------------------------

class AnthropicSummarization:
    """
    Summarization provider using Anthropic's Claude API.

    Authentication (checked in priority order):
    1. api_key parameter (if provided)
    2. ANTHROPIC_API_KEY (recommended: API key from console.anthropic.com)
    3. CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token')

    Note: OAuth tokens (sk-ant-oat01-...) are primarily for Claude Code CLI.
    For production use, prefer API keys (sk-ant-api03-...) from console.anthropic.com.

    Default model is claude-3-haiku (cost-effective: $0.25/$1.25 per MTok).
    Configure via keep.toml [summarization] section for other models:
    - claude-3-haiku-20240307: Cost-effective, fast, good for summaries
    - claude-3-5-haiku-20241022: Better quality, higher cost
    - claude-haiku-4-5-20251001: Latest, best quality, highest cost
    """

    def __init__(
        self,
        model: str = "claude-3-haiku-20240307",
        api_key: str | None = None,
        max_tokens: int = 150,
    ):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise RuntimeError("AnthropicSummarization requires 'anthropic' library")

        self.model = model
        self.max_tokens = max_tokens

        # Try multiple auth sources in priority order:
        # 1. Explicit api_key parameter
        # 2. ANTHROPIC_API_KEY (API key from console.anthropic.com: sk-ant-api03-...)
        # 3. CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token': sk-ant-oat01-...)
        key = (
            api_key or
            os.environ.get("ANTHROPIC_API_KEY") or
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        )
        if not key:
            raise ValueError(
                "Anthropic authentication required. Set one of:\n"
                "  ANTHROPIC_API_KEY (API key from console.anthropic.com)\n"
                "  CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token')"
            )

        self.client = Anthropic(api_key=key)
    
    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        """Generate summary using Anthropic Claude."""
        # Truncate very long content
        truncated = content[:50000] if len(content) > 50000 else content

        # Build prompt with optional context
        prompt = build_summarization_prompt(truncated, context)

        # Use base system prompt when context is included in user message
        system = SUMMARIZATION_SYSTEM_PROMPT if not context else (
            "You are a helpful assistant that summarizes documents. "
            "Follow the instructions in the user message."
        )

        # The Anthropic SDK has built-in retry with exponential backoff for rate limits.
        # Let rate limit errors propagate so pending queue can retry later.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        # Extract text from response
        if response.content and len(response.content) > 0:
            return strip_summary_preamble(response.content[0].text)
        return truncated[:500]  # Fallback for empty response


class OpenAISummarization:
    """
    Summarization provider using OpenAI's chat API.

    Requires: KEEP_OPENAI_API_KEY or OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        max_tokens: int = 200,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("OpenAISummarization requires 'openai' library")

        self.model = model
        self.max_tokens = max_tokens

        key = api_key or os.environ.get("KEEP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI API key required")

        self._client = OpenAI(api_key=key)

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        """Generate a summary using OpenAI."""
        # Truncate very long content to avoid token limits
        truncated = content[:50000] if len(content) > 50000 else content

        # Build prompt with optional context
        prompt = build_summarization_prompt(truncated, context)

        # Use base system prompt when context is included in user message
        system = SUMMARIZATION_SYSTEM_PROMPT if not context else (
            "You are a helpful assistant that summarizes documents. "
            "Follow the instructions in the user message."
        )

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.max_tokens,
            temperature=0.3,
        )

        return strip_summary_preamble(response.choices[0].message.content.strip())


class OllamaSummarization:
    """
    Summarization provider using Ollama's local API.

    Respects OLLAMA_HOST env var (default: http://localhost:11434).
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str | None = None,
    ):
        self.model = model
        if base_url is None:
            base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        if not base_url.startswith("http"):
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        """Generate a summary using Ollama."""
        import requests

        truncated = content[:50000] if len(content) > 50000 else content

        # Build prompt with optional context
        prompt = build_summarization_prompt(truncated, context)

        # Use base system prompt when context is included in user message
        system = SUMMARIZATION_SYSTEM_PROMPT if not context else (
            "You are a helpful assistant that summarizes documents. "
            "Follow the instructions in the user message."
        )

        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
        )
        response.raise_for_status()

        return strip_summary_preamble(response.json()["message"]["content"].strip())


class GeminiSummarization:
    """
    Summarization provider using Google's Gemini API.

    Requires: GEMINI_API_KEY or GOOGLE_API_KEY environment variable.
    """

    def __init__(
        self,
        model: str = "gemini-3-flash-preview",
        api_key: str | None = None,
        max_tokens: int = 150,
    ):
        try:
            from google import genai
        except ImportError:
            raise RuntimeError("GeminiSummarization requires 'google-genai' library")

        self.model = model
        self.max_tokens = max_tokens

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError("Gemini API key required. Set GEMINI_API_KEY or GOOGLE_API_KEY")

        self._client = genai.Client(api_key=key)

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        """Generate summary using Google Gemini."""
        truncated = content[:50000] if len(content) > 50000 else content

        # Build prompt with optional context
        prompt = build_summarization_prompt(truncated, context)

        # Let errors propagate so pending queue can retry
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return strip_summary_preamble(response.text)


class PassthroughSummarization:
    """
    Summarization provider that returns the first N characters.

    Useful for testing or when LLM summarization is not needed.
    """

    def __init__(self, max_chars: int = 500):
        self.max_chars = max_chars

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        """Return truncated content as summary (ignores context)."""
        limit = min(self.max_chars, max_length)
        if len(content) <= limit:
            return content
        return content[:limit].rsplit(" ", 1)[0] + "..."


# -----------------------------------------------------------------------------
# Tagging Providers
# -----------------------------------------------------------------------------

class AnthropicTagging:
    """
    Tagging provider using Anthropic's Claude API with JSON output.

    Authentication (checked in priority order):
    1. api_key parameter (if provided)
    2. ANTHROPIC_API_KEY (recommended: API key from console.anthropic.com)
    3. CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token')

    Default model is claude-3-haiku (cost-effective). See AnthropicSummarization
    for model options and pricing.
    """

    SYSTEM_PROMPT = """Analyze the document and generate relevant tags as a JSON object.

Generate tags for these categories when applicable:
- content_type: The type of content (e.g., "documentation", "code", "article", "config")
- language: Programming language if code (e.g., "python", "javascript")
- domain: Subject domain (e.g., "authentication", "database", "api", "testing")
- framework: Framework or library if relevant (e.g., "react", "django", "fastapi")

Only include tags that clearly apply. Values should be lowercase.

Respond with a JSON object only, no explanation."""

    def __init__(
        self,
        model: str = "claude-3-haiku-20240307",
        api_key: str | None = None,
    ):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise RuntimeError("AnthropicTagging requires 'anthropic' library")

        self.model = model

        # Try multiple auth sources (same as AnthropicSummarization):
        # 1. Explicit api_key parameter
        # 2. ANTHROPIC_API_KEY (API key from console.anthropic.com: sk-ant-api03-...)
        # 3. CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token': sk-ant-oat01-...)
        key = (
            api_key or
            os.environ.get("ANTHROPIC_API_KEY") or
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        )
        if not key:
            raise ValueError(
                "Anthropic authentication required. Set one of:\n"
                "  ANTHROPIC_API_KEY (API key from console.anthropic.com)\n"
                "  CLAUDE_CODE_OAUTH_TOKEN (OAuth token from 'claude setup-token')"
            )

        self._client = Anthropic(api_key=key)
    
    def tag(self, content: str) -> dict[str, str]:
        """Generate tags using Anthropic Claude."""
        truncated = content[:20000] if len(content) > 20000 else content
        
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=200,
                temperature=0.2,
                system=self.SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": truncated}
                ],
            )
            
            # Parse JSON from response
            if response.content and len(response.content) > 0:
                tags = json.loads(response.content[0].text)
                return {str(k): str(v) for k, v in tags.items()}
            return {}
        except (json.JSONDecodeError, Exception):
            return {}


class OpenAITagging:
    """
    Tagging provider using OpenAI's chat API with JSON output.
    """
    
    SYSTEM_PROMPT = """Analyze the document and generate relevant tags as a JSON object.

Generate tags for these categories when applicable:
- content_type: The type of content (e.g., "documentation", "code", "article", "config")
- language: Programming language if code (e.g., "python", "javascript")
- domain: Subject domain (e.g., "authentication", "database", "api", "testing")
- framework: Framework or library if relevant (e.g., "react", "django", "fastapi")

Only include tags that clearly apply. Values should be lowercase.

Respond with a JSON object only, no explanation."""
    
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("OpenAITagging requires 'openai' library")
        
        self.model = model
        
        key = api_key or os.environ.get("KEEP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI API key required")
        
        self._client = OpenAI(api_key=key)
    
    def tag(self, content: str) -> dict[str, str]:
        """Generate tags using OpenAI."""
        truncated = content[:20000] if len(content) > 20000 else content
        
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0.2,
        )
        
        try:
            tags = json.loads(response.choices[0].message.content)
            # Ensure all values are strings
            return {str(k): str(v) for k, v in tags.items()}
        except json.JSONDecodeError:
            return {}


class OllamaTagging:
    """
    Tagging provider using Ollama's local API.

    Respects OLLAMA_HOST env var (default: http://localhost:11434).
    """

    SYSTEM_PROMPT = OpenAITagging.SYSTEM_PROMPT

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str | None = None,
    ):
        self.model = model
        if base_url is None:
            base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        if not base_url.startswith("http"):
            base_url = f"http://{base_url}"
        self.base_url = base_url.rstrip("/")
    
    def tag(self, content: str) -> dict[str, str]:
        """Generate tags using Ollama."""
        import requests
        
        truncated = content[:20000] if len(content) > 20000 else content
        
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": truncated},
                ],
                "format": "json",
                "stream": False,
            },
        )
        response.raise_for_status()
        
        try:
            tags = json.loads(response.json()["message"]["content"])
            return {str(k): str(v) for k, v in tags.items()}
        except (json.JSONDecodeError, KeyError):
            return {}


class NoopTagging:
    """
    Tagging provider that returns empty tags.
    
    Useful when tagging is disabled or for testing.
    """
    
    def tag(self, content: str) -> dict[str, str]:
        """Return empty tags."""
        return {}


# Register providers
_registry = get_registry()
_registry.register_summarization("anthropic", AnthropicSummarization)
_registry.register_summarization("openai", OpenAISummarization)
_registry.register_summarization("ollama", OllamaSummarization)
_registry.register_summarization("gemini", GeminiSummarization)
_registry.register_summarization("passthrough", PassthroughSummarization)
_registry.register_tagging("anthropic", AnthropicTagging)
_registry.register_tagging("openai", OpenAITagging)
_registry.register_tagging("ollama", OllamaTagging)
_registry.register_tagging("noop", NoopTagging)
