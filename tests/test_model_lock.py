"""Tests for model-lock wrappers."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from keep.model_lock import LockedEmbeddingProvider


class SlowEmbeddingProvider:
    """Embedding provider that records concurrent call count."""

    model_name = "slow"
    dimension = 3

    def __init__(self):
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def embed(self, _text: str) -> list[float]:
        with self._lock:
            self._active += 1
            if self._active > self.max_active:
                self.max_active = self._active
        try:
            time.sleep(0.01)
            return [1.0, 2.0, 3.0]
        finally:
            with self._lock:
                self._active -= 1

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def test_locked_embedding_provider_serializes_threads(tmp_path):
    """File lock alone is not enough; wrapper must serialize in-process threads."""
    base = SlowEmbeddingProvider()
    wrapped = LockedEmbeddingProvider(base, tmp_path / "embed.lock")

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(wrapped.embed, [f"t{i}" for i in range(24)]))

    assert base.max_active == 1
