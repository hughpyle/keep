"""Shared OpenAI client factory for OpenAI and compatible endpoints.

Supports two modes:
1. OpenAI API: requires KEEP_OPENAI_API_KEY or OPENAI_API_KEY
2. Local server (llama-server, vLLM, etc.): base_url set, no key needed
"""

import os


def create_openai_client(
    api_key: str | None = None,
    base_url: str | None = None,
):
    """Create an OpenAI client for the OpenAI API or a compatible local server.

    Args:
        api_key: Explicit API key (overrides environment variables).
            Not required when *base_url* points at a local server.
        base_url: Override the API base URL (e.g.
            ``http://localhost:8801/v1`` for llama-server).

    Returns:
        openai.OpenAI client instance

    Raises:
        RuntimeError: If the openai library is not installed.
        ValueError: If no API key is available and no base_url is set.
    """
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "OpenAI provider requires the 'openai' library. "
            "Install with: pip install openai"
        )

    key = (
        api_key
        or os.environ.get("KEEP_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not key and not base_url:
        raise ValueError(
            "OpenAI API key required. Set KEEP_OPENAI_API_KEY or OPENAI_API_KEY"
        )

    # The SDK requires a non-empty api_key; local servers ignore it.
    return OpenAI(api_key=key or "not-required", base_url=base_url)
