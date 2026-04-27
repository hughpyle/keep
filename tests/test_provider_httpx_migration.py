"""Regression tests for provider HTTP calls using httpx."""

import httpx
import pytest

from keep.providers.embeddings import VoyageEmbedding
from keep.providers.ollama_utils import ollama_ensure_model
from keep.types import user_agent


def test_shared_http_session_uses_httpx_and_user_agent():
    from keep.providers import http as provider_http

    provider_http.close_http_session()
    session = provider_http.http_session()
    try:
        assert isinstance(session, httpx.Client)
        assert session.headers["User-Agent"] == user_agent()
    finally:
        provider_http.close_http_session()


def test_ollama_ensure_model_uses_shared_httpx_session(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "nomic-embed-text:latest"}]}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, *, timeout):
            self.calls.append((url, timeout))
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr("keep.providers.ollama_utils.ollama_session", lambda: session)

    ollama_ensure_model("http://localhost:11434", "nomic-embed-text")

    assert session.calls == [("http://localhost:11434/api/tags", 5)]


def test_voyage_request_errors_are_reported(monkeypatch):
    class FailingSession:
        def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://api.voyageai.com/v1/embeddings")
            raise httpx.ConnectError("network unreachable", request=request)

    monkeypatch.setattr("keep.providers.http.http_session", lambda: FailingSession())
    provider = VoyageEmbedding(model="voyage-3-lite", api_key="test-key")

    with pytest.raises(RuntimeError, match="Cannot reach Voyage AI API"):
        provider.embed("hello")
