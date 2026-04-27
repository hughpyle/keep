"""Shared HTTP session for connection pooling across all providers."""

import httpx

from keep.types import user_agent

_session: httpx.Client | None = None


def http_session() -> httpx.Client:
    """Return a shared httpx client for all provider HTTP calls.

    Reuses TCP connections across embedding, summarization, tagging,
    document fetch, and other HTTP-backed provider calls.
    """
    global _session
    if _session is None:
        _session = httpx.Client(headers={"User-Agent": user_agent()})
    return _session


def close_http_session() -> None:
    """Close the shared session, interrupting any in-flight requests.

    Called during shutdown to unblock threads stuck in socket reads.
    """
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
        _session = None
