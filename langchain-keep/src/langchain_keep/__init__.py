"""LangChain integration for keep — reflective memory for AI agents.

All code lives in keep.langchain. This package is a discovery shim
so that ``pip install langchain-keep`` works for LangChain users.

Usage::

    from langchain_keep import KeepStore, KeepNotesToolkit

    # Or equivalently:
    from keep.langchain import KeepStore, KeepNotesToolkit
"""

from keep.langchain import (
    KeepNotesMiddleware,
    KeepNotesRetriever,
    KeepNotesToolkit,
    KeepStore,
)

__all__ = [
    "KeepStore",
    "KeepNotesToolkit",
    "KeepNotesRetriever",
    "KeepNotesMiddleware",
]
