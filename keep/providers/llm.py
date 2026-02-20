"""
Summarization and tagging providers using LLMs.
"""

import json
import os

from .base import (
    get_registry,
    build_summarization_prompt,
    get_summarization_system_prompt,
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

    Default model is claude-haiku-4.5 ($1.00/$5.00 per MTok).
    Configure via keep.toml [summarization] section for other models:
    - claude-haiku-4-5-20251001: Default, best quality/cost for summaries
    - claude-3-5-haiku-20241022: Previous generation
    - claude-3-haiku-20240307: Legacy, cheapest ($0.25/$1.25 per MTok)
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
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

        # Auto-detect content type and select appropriate system prompt
        system = get_summarization_system_prompt(truncated) if not context else (
            "You are a helpful assistant that summarizes content. "
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

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        """Send a raw prompt to Anthropic and return generated text."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.content:
            return response.content[0].text
        return None


class OpenAISummarization:
    """
    Summarization provider using OpenAI's chat API.

    Requires: KEEP_OPENAI_API_KEY or OPENAI_API_KEY environment variable.

    Default model is gpt-4.1-mini ($0.40/$1.60 per MTok).
    Good alternatives: gpt-4.1, gpt-5-mini.
    """

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
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
            raise ValueError(
                "OpenAI API key required. Set KEEP_OPENAI_API_KEY or OPENAI_API_KEY"
            )

        self._client = OpenAI(api_key=key)

        # GPT-5+ and reasoning models use a different API surface:
        # - max_completion_tokens instead of max_tokens
        # - temperature must be omitted (only default=1 supported)
        self._new_api = self.model.startswith(("gpt-5", "o3", "o4"))

    def _completion_kwargs(self, max_tokens: int) -> dict:
        """Return model-appropriate kwargs for token limit and temperature."""
        if self._new_api:
            return {"max_completion_tokens": max_tokens}
        return {"max_tokens": max_tokens, "temperature": 0.3}

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

        # Auto-detect content type and select appropriate system prompt
        system = get_summarization_system_prompt(truncated) if not context else (
            "You are a helpful assistant that summarizes content. "
            "Follow the instructions in the user message."
        )

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **self._completion_kwargs(self.max_tokens),
        )

        return strip_summary_preamble(response.choices[0].message.content.strip())

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        """Send a raw prompt to OpenAI and return generated text."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self._completion_kwargs(max_tokens),
        )
        if response.choices:
            return response.choices[0].message.content
        return None


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
        from .ollama_utils import ollama_base_url, ollama_ensure_model
        self.base_url = ollama_base_url(base_url)
        ollama_ensure_model(self.base_url, self.model)

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

        # Auto-detect content type and select appropriate system prompt
        system = get_summarization_system_prompt(truncated) if not context else (
            "You are a helpful assistant that summarizes content. "
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
            timeout=(10, 120),  # (connect, read)
        )
        if not response.ok:
            detail = response.text[:200] if response.text else ""
            raise RuntimeError(
                f"Ollama summarization failed (model={self.model}): "
                f"HTTP {response.status_code} from {self.base_url}. {detail}"
            )

        return strip_summary_preamble(response.json()["message"]["content"].strip())

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        """Send a raw prompt to Ollama and return generated text."""
        import requests

        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            timeout=(10, 300),  # (connect, read) — generation can be slow
        )
        if not response.ok:
            detail = response.text[:200] if response.text else ""
            raise RuntimeError(
                f"Ollama generate failed (model={self.model}): "
                f"HTTP {response.status_code} from {self.base_url}. {detail}"
            )
        return response.json()["message"]["content"].strip()


class GeminiSummarization:
    """
    Summarization provider using Google's Gemini API.

    Authentication (checked in priority order):
    1. api_key parameter (if provided, uses Google AI Studio)
    2. GOOGLE_CLOUD_PROJECT env var (uses Vertex AI with ADC)
    3. GEMINI_API_KEY or GOOGLE_API_KEY (uses Google AI Studio)

    Default model is gemini-2.5-flash (cost-effective: ~$0.075/$0.30 per MTok).
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
        max_tokens: int = 150,
    ):
        from .gemini_client import create_gemini_client

        self.model = model
        self.max_tokens = max_tokens
        self._client = create_gemini_client(api_key)

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

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        """Send a raw prompt to Gemini and return generated text."""
        full_prompt = f"{system}\n\n{user}"
        response = self._client.models.generate_content(
            model=self.model,
            contents=full_prompt,
        )
        return response.text


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

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        """Passthrough has no LLM — return None."""
        return None


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
        model: str = "claude-haiku-4-5-20251001",
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
        import logging
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
        except Exception as e:
            logging.getLogger(__name__).warning("Anthropic tagging failed: %s", e)
            return {}

        # Parse JSON from response
        if response.content and len(response.content) > 0:
            try:
                tags = json.loads(response.content[0].text)
                return {str(k): str(v) for k, v in tags.items()}
            except json.JSONDecodeError:
                return {}
        return {}


class OpenAITagging:
    """
    Tagging provider using OpenAI's chat API with JSON output.
    """
    
    SYSTEM_PROMPT = AnthropicTagging.SYSTEM_PROMPT

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        api_key: str | None = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("OpenAITagging requires 'openai' library")
        
        self.model = model
        
        key = api_key or os.environ.get("KEEP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "OpenAI API key required. Set KEEP_OPENAI_API_KEY or OPENAI_API_KEY"
            )

        self._client = OpenAI(api_key=key)
        self._new_api = self.model.startswith(("gpt-5", "o3", "o4"))

    def tag(self, content: str) -> dict[str, str]:
        """Generate tags using OpenAI."""
        truncated = content[:20000] if len(content) > 20000 else content

        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            "response_format": {"type": "json_object"},
        }
        if self._new_api:
            kwargs["max_completion_tokens"] = 200
        else:
            kwargs["max_tokens"] = 200
            kwargs["temperature"] = 0.2

        response = self._client.chat.completions.create(**kwargs)
        
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
        from .ollama_utils import ollama_base_url, ollama_ensure_model
        self.base_url = ollama_base_url(base_url)
        ollama_ensure_model(self.base_url, self.model)

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
            timeout=120,
        )
        if not response.ok:
            detail = response.text[:200] if response.text else ""
            raise RuntimeError(
                f"Ollama tagging failed (model={self.model}): "
                f"HTTP {response.status_code} from {self.base_url}. {detail}"
            )

        try:
            tags = json.loads(response.json()["message"]["content"])
            return {str(k): str(v) for k, v in tags.items()}
        except (json.JSONDecodeError, KeyError):
            return {}


class GeminiTagging:
    """
    Tagging provider using Google's Gemini API with JSON output.

    Authentication (checked in priority order):
    1. api_key parameter (if provided, uses Google AI Studio)
    2. GOOGLE_CLOUD_PROJECT env var (uses Vertex AI with ADC)
    3. GEMINI_API_KEY or GOOGLE_API_KEY (uses Google AI Studio)
    """

    SYSTEM_PROMPT = OpenAITagging.SYSTEM_PROMPT

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        api_key: str | None = None,
    ):
        from .gemini_client import create_gemini_client

        self.model = model
        self._client = create_gemini_client(api_key)

    def tag(self, content: str) -> dict[str, str]:
        """Generate tags using Google Gemini."""
        import logging
        truncated = content[:20000] if len(content) > 20000 else content

        try:
            full_prompt = f"{self.SYSTEM_PROMPT}\n\n{truncated}"
            response = self._client.models.generate_content(
                model=self.model,
                contents=full_prompt,
            )
        except Exception as e:
            logging.getLogger(__name__).warning("Gemini tagging failed: %s", e)
            return {}

        # Parse JSON from response
        try:
            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3].strip()

            tags = json.loads(text)
            return {str(k): str(v) for k, v in tags.items()}
        except json.JSONDecodeError:
            return {}


class NoopTagging:
    """
    Tagging provider that returns empty tags.

    Useful when tagging is disabled or for testing.
    """

    def tag(self, content: str) -> dict[str, str]:
        """Return empty tags."""
        return {}


# -----------------------------------------------------------------------------
# Media Description Providers
# -----------------------------------------------------------------------------

class OllamaMediaDescriber:
    """
    Media description using Ollama's vision models.

    Supports image description via multimodal models (llava, moondream, etc.).
    Audio transcription is not supported via Ollama.

    Respects OLLAMA_HOST env var (default: http://localhost:11434).
    """

    IMAGE_PROMPT = (
        "Describe this image in detail. Include the subject, setting, "
        "colors, composition, and any text visible in the image. "
        "Be specific and factual."
    )

    def __init__(
        self,
        model: str = "llava",
        base_url: str | None = None,
    ):
        self.model = model
        from .ollama_utils import ollama_base_url, ollama_ensure_model
        self.base_url = ollama_base_url(base_url)
        ollama_ensure_model(self.base_url, self.model)

    def describe(self, path: str, content_type: str) -> str | None:
        """Describe an image using Ollama vision model."""
        if not content_type.startswith("image/"):
            return None

        import base64
        import requests

        with open(path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": self.IMAGE_PROMPT,
                        "images": [image_data],
                    },
                ],
                "stream": False,
            },
            timeout=120,
        )
        if not response.ok:
            detail = response.text[:200] if response.text else ""
            raise RuntimeError(
                f"Ollama vision failed (model={self.model}): "
                f"HTTP {response.status_code} from {self.base_url}. {detail}"
            )

        text = response.json()["message"]["content"].strip()
        return text if text else None


class OllamaContentExtractor:
    """
    OCR content extraction using Ollama.

    Uses GLM-OCR via Ollama's /api/generate endpoint (recommended for
    vision tasks with this model). Extracts actual text from document images.

    Respects OLLAMA_HOST env var (default: http://localhost:11434).
    """

    OCR_PROMPT = "Extract all text from this image exactly as written."

    def __init__(
        self,
        model: str = "glm-ocr",
        base_url: str | None = None,
    ):
        self.model = model
        from .ollama_utils import ollama_base_url, ollama_ensure_model
        self.base_url = ollama_base_url(base_url)
        ollama_ensure_model(self.base_url, self.model)

    def extract(self, path: str, content_type: str) -> str | None:
        """Extract text from an image using Ollama OCR model."""
        if not content_type.startswith("image/"):
            return None

        import base64
        import requests

        with open(path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": self.OCR_PROMPT,
                "images": [image_data],
                "stream": False,
            },
            timeout=120,
        )
        if not response.ok:
            detail = response.text[:200] if response.text else ""
            raise RuntimeError(
                f"Ollama OCR failed (model={self.model}): "
                f"HTTP {response.status_code} from {self.base_url}. {detail}"
            )

        text = response.json().get("response", "").strip()
        return text if len(text) > 10 else None


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
_registry.register_tagging("gemini", GeminiTagging)
_registry.register_tagging("noop", NoopTagging)
_registry.register_media("ollama", OllamaMediaDescriber)
_registry.register_content_extractor("ollama", OllamaContentExtractor)
