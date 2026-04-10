"""OpenRouter providers for embeddings and summarization."""

from __future__ import annotations

import os

from .base import (
    EmbedTask,
    SUMMARIZATION_SYSTEM_PROMPT,
    build_summarization_prompt,
    get_registry,
    require_provider_param,
    strip_summary_preamble,
)
from .openai_client import create_openai_client

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_OPENROUTER_PREFIX_ALIASES = {
    "openai": "openai",
    "google": "google",
    "anthropic": "anthropic",
    "mistralai": "mistralai",
    "mistral": "mistralai",
    "voyage": "voyage",
}

_OPENROUTER_BARE_PREFIXES = (
    ("text-embedding-", "openai"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "google"),
    ("embedding-001", "google"),
    ("mistral", "mistralai"),
    ("ministral", "mistralai"),
    ("codestral", "mistralai"),
    ("pixtral", "mistralai"),
    ("voyage-", "voyage"),
)


def canonicalize_openrouter_model(model: str) -> str:
    """Return a stable OpenRouter model name.

    Bare names for common provider families are normalized to OpenRouter's
    prefixed form so config, cache keys, and diagnostics stay aligned.
    Unknown bare names are left unchanged.
    """
    if "/" in model:
        prefix, rest = model.split("/", 1)
        canonical_prefix = _OPENROUTER_PREFIX_ALIASES.get(prefix.lower())
        if canonical_prefix and rest:
            return f"{canonical_prefix}/{rest}"
        return model

    for prefix, provider in _OPENROUTER_BARE_PREFIXES:
        if model.startswith(prefix):
            return f"{provider}/{model}"
    return model


def _resolve_openrouter_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError(
            "OpenRouter API key required. Set OPENROUTER_API_KEY.\n"
            "Get your API key at: https://openrouter.ai/settings/keys"
        )
    return key


def _openrouter_headers(
    site_url: str | None,
    app_name: str | None,
) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    if site_url:
        # Strip CRLF to prevent header injection (httpx also rejects these)
        headers["HTTP-Referer"] = site_url.replace("\r", "").replace("\n", "").strip()
    if app_name:
        headers["X-OpenRouter-Title"] = app_name.replace("\r", "").replace("\n", "").strip()
    return headers or None


def _create_openrouter_client(
    *,
    api_key: str | None,
    base_url: str | None,
    site_url: str | None,
    app_name: str | None,
):
    # SSRF prevention: base_url is validated inside create_openai_client
    return create_openai_client(
        api_key=_resolve_openrouter_key(api_key),
        base_url=base_url or OPENROUTER_BASE_URL,
        default_headers=_openrouter_headers(site_url, app_name),
    )


class OpenRouterEmbedding:
    """Embedding provider using OpenRouter's OpenAI-compatible API."""

    MODEL_DIMENSIONS = {
        "openai/text-embedding-3-small": 1536,
        "openai/text-embedding-3-large": 3072,
    }

    _INPUT_TYPES = {
        EmbedTask.DOCUMENT: "search_document",
        EmbedTask.QUERY: "search_query",
    }

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        dimensions: int | None = None,
        site_url: str | None = None,
        app_name: str | None = None,
    ):
        model = require_provider_param(model, provider="OpenRouterEmbedding")
        self.model_name = canonicalize_openrouter_model(model)
        self._dimension = dimensions or self.MODEL_DIMENSIONS.get(self.model_name)
        self._dimensions = dimensions
        self._client = _create_openrouter_client(
            api_key=api_key,
            base_url=base_url,
            site_url=site_url,
            app_name=app_name,
        )

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed("dimension test"))
        return self._dimension

    def _request_kwargs(self, task: EmbedTask) -> dict:
        kwargs: dict = {
            "model": self.model_name,
            "extra_body": {"input_type": self._INPUT_TYPES[task]},
        }
        if self._dimensions is not None:
            kwargs["dimensions"] = self._dimensions
        return kwargs

    def embed(self, text: str, *, task: EmbedTask = EmbedTask.DOCUMENT) -> list[float]:
        response = self._client.embeddings.create(
            input=text,
            **self._request_kwargs(task),
        )
        embedding = response.data[0].embedding
        if self._dimension is None:
            self._dimension = len(embedding)
        return embedding

    def embed_batch(
        self,
        texts: list[str],
        *,
        task: EmbedTask = EmbedTask.DOCUMENT,
    ) -> list[list[float]]:
        response = self._client.embeddings.create(
            input=texts,
            **self._request_kwargs(task),
        )
        sorted_data = sorted(response.data, key=lambda x: x.index)
        embeddings = [d.embedding for d in sorted_data]
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])
        return embeddings


class OpenRouterSummarization:
    """Summarization provider using OpenRouter's chat completions API."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 200,
        site_url: str | None = None,
        app_name: str | None = None,
    ):
        model = require_provider_param(model, provider="OpenRouterSummarization")
        self.model_name = canonicalize_openrouter_model(model)
        self.max_tokens = max_tokens
        self._client = _create_openrouter_client(
            api_key=api_key,
            base_url=base_url,
            site_url=site_url,
            app_name=app_name,
        )

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        truncated = content[:50000] if len(content) > 50000 else content
        prompt = build_summarization_prompt(truncated, context)
        result = self.generate(system_prompt or SUMMARIZATION_SYSTEM_PROMPT, prompt)
        if not result:
            return truncated[:max_length]
        return strip_summary_preamble(result)

    def generate(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
    ) -> str | None:
        response = self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        if response.choices:
            return response.choices[0].message.content
        return None


_registry = get_registry()
_registry.register_embedding("openrouter", OpenRouterEmbedding)
_registry.register_summarization("openrouter", OpenRouterSummarization)
