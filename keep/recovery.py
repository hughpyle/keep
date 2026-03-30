"""Helpers for malformed note-database recovery."""

from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_malformed_db_error(exc: BaseException) -> bool:
    """Return whether *exc* looks like a malformed SQLite database error."""
    return "malformed" in str(exc).lower()


def run_with_document_store_recovery(
    fn: Callable[[], T],
    *,
    get_document_store: Callable[[], Any] | None,
    operation: str,
) -> T:
    """Run *fn*, requesting document-store recovery once on malformed errors."""
    try:
        return fn()
    except Exception as exc:
        if not is_malformed_db_error(exc):
            raise

        document_store = get_document_store() if callable(get_document_store) else None
        recover = getattr(document_store, "_try_runtime_recover", None)
        if not callable(recover):
            raise

        logger.warning("%s hit malformed note database; triggering runtime recovery", operation)
        if not recover():
            raise

        logger.warning("%s retrying after note database recovery", operation)
        try:
            return fn()
        except Exception as retry_exc:
            logger.warning(
                "%s failed after note database recovery retry: %s",
                operation,
                retry_exc,
            )
            raise
