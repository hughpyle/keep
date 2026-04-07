"""Helpers for normalizing provider identity fields."""

from __future__ import annotations

from typing import Any


def provider_model_name(provider: Any, default: str = "unknown") -> str:
    """Resolve a provider's configured model name from legacy or current attrs.

    ``model_name`` is the preferred interface for providers. Some older or
    lightly mocked providers still expose ``model`` instead. Centralize that
    fallback here so cache keys, identity recording, diagnostics, and wrapper
    passthrough all agree.
    """
    model_name = getattr(provider, "model_name", None)
    if isinstance(model_name, str) and model_name:
        return model_name

    model = getattr(provider, "model", None)
    if isinstance(model, str) and model:
        return model

    return default
