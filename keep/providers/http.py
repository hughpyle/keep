"""Shared HTTP session for connection pooling across all providers."""

import requests

_session: requests.Session | None = None


def http_session() -> requests.Session:
    """Return a shared requests.Session for all provider HTTP calls.

    Reuses TCP connections across embedding, summarization, tagging,
    document fetch, and other HTTP-backed provider calls.
    """
    global _session
    if _session is None:
        from keep.types import user_agent

        _session = requests.Session()
        _session.headers["User-Agent"] = user_agent()
    return _session
