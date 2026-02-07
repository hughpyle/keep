"""
Cross-process model lock using fcntl.flock.

Prevents multiple keep processes from loading MLX models simultaneously,
which would exhaust GPU memory on Apple Silicon.

Uses POSIX advisory file locks which are automatically released on
process exit (including crashes), so stale locks cannot occur.
"""

import gc
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# fcntl is only available on POSIX systems
_has_fcntl = sys.platform != "win32"
if _has_fcntl:
    import fcntl


class ModelLock:
    """
    Advisory file lock for serializing model access across processes.

    Uses fcntl.flock which is:
    - Automatically released on process exit/crash
    - Blocking by default (waits until lock available)
    - Safe on macOS and Linux

    On non-POSIX platforms, acquire/release are no-ops.
    """

    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, wait until lock is available.
                      If False, return False immediately if lock is held.

        Returns:
            True if lock was acquired, False if non-blocking and lock is held.
        """
        if not _has_fcntl:
            return True

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)

        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB

        try:
            fcntl.flock(self._fd, flags)
            logger.debug("Acquired model lock: %s", self._lock_path.name)
            return True
        except (OSError, BlockingIOError):
            os.close(self._fd)
            self._fd = None
            return False

    def release(self) -> None:
        """Release the lock."""
        if not _has_fcntl or self._fd is None:
            return

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        logger.debug("Released model lock: %s", self._lock_path.name)

    def is_locked(self) -> bool:
        """
        Probe whether the lock is currently held by another process.

        Does not acquire the lock. Returns True if held, False if available.
        """
        if not _has_fcntl:
            return False

        if not self._lock_path.exists():
            return False

        fd = None
        try:
            fd = os.open(str(self._lock_path), os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Got the lock — nobody holds it. Release immediately.
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (OSError, BlockingIOError):
            # Lock is held by another process.
            return True
        finally:
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class LockedEmbeddingProvider:
    """
    Lifecycle-locked wrapper for an embedding provider.

    Acquires a file lock on first embed() call and holds it for the
    entire time the model is loaded in GPU memory. On release(), the
    inner provider is deleted and gc.collect() is called before the
    flock is released, ensuring GPU memory is freed before another
    process can load its own copy.

    The CachingEmbeddingProvider should wrap OUTSIDE this class so
    that cache hits never touch the lock or the model.
    """

    def __init__(self, provider, lock_path: Path):
        self._provider = provider
        self._lock = ModelLock(lock_path)
        self._acquired = False

    def _ensure_lock(self) -> None:
        if not self._acquired:
            self._lock.acquire(blocking=True)
            self._acquired = True

    @property
    def model_name(self) -> str:
        return getattr(self._provider, "model_name", "unknown")

    @property
    def dimension(self) -> int:
        self._ensure_lock()
        return self._provider.dimension

    def embed(self, text: str) -> list[float]:
        self._ensure_lock()
        return self._provider.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._ensure_lock()
        return self._provider.embed_batch(texts)

    def release(self) -> None:
        """Free the model and release the lock."""
        if not self._acquired:
            return

        # Delete provider to free model memory
        try:
            del self._provider
        except AttributeError:
            pass
        self._provider = None

        # Force garbage collection to reclaim GPU memory
        gc.collect()

        # Now release the lock
        self._lock.release()
        self._acquired = False
        logger.debug("Released embedding provider and lock")


class LockedSummarizationProvider:
    """
    Lifecycle-locked wrapper for a summarization provider.

    Same pattern as LockedEmbeddingProvider — holds the lock while
    the model is loaded, releases model memory before releasing the lock.
    """

    def __init__(self, provider, lock_path: Path):
        self._provider = provider
        self._lock = ModelLock(lock_path)
        self._acquired = False

    def _ensure_lock(self) -> None:
        if not self._acquired:
            self._lock.acquire(blocking=True)
            self._acquired = True

    @property
    def model_name(self) -> str:
        return getattr(self._provider, "model_name", "unknown")

    def summarize(
        self,
        content: str,
        *,
        max_length: int = 500,
        context: str | None = None,
    ) -> str:
        self._ensure_lock()
        return self._provider.summarize(
            content, max_length=max_length, context=context
        )

    def release(self) -> None:
        """Free the model and release the lock."""
        if not self._acquired:
            return

        try:
            del self._provider
        except AttributeError:
            pass
        self._provider = None

        gc.collect()

        self._lock.release()
        self._acquired = False
        logger.debug("Released summarization provider and lock")
