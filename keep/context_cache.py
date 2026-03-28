"""In-memory action-level cache for get_context() components.

Caches action results at the flow engine's action runner, keyed by
action name + params.  Three specialized caches handle different
invalidation strategies:

- SimilarCache: generation + TTL (find similar_to queries)
- MetaCache: generation-based (resolve_meta)
- PartsCache: item-based (find prefix@p queries)

The ContextCache orchestrator routes check/store/invalidation to
the appropriate sub-cache.

Stores IDs+scores (not full Items) for similar and meta results;
hydrates fresh item data on cache hit.  Parts results are stored
in full since parts aren't regular items.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .actions import item_to_result
from .types import is_part_id

logger = logging.getLogger(__name__)

_ACTION_FIND = "find"
_ACTION_RESOLVE_META = "resolve_meta"


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def _cache_key(action_name: str, params: dict[str, Any]) -> str:
    """Deterministic cache key from action name + params."""
    try:
        canonical = json.dumps(
            {"a": action_name, **params},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Hydration helpers
# ---------------------------------------------------------------------------

def _hydrate_find_results(
    ids_scores: list[tuple[str, float | None]],
    ctx: Any,
) -> dict[str, Any]:
    """Rebuild find action output from cached IDs+scores."""
    results = []
    for item_id, score in ids_scores:
        item = ctx.get(item_id)
        if item is not None:
            r = item_to_result(item)
            r["score"] = score  # override with cached score
            results.append(r)
    return {"results": results, "count": len(results)}


def _hydrate_meta_results(
    sections: dict[str, list[tuple[str, float | None]]],
    ctx: Any,
) -> dict[str, Any]:
    """Rebuild resolve_meta output from cached section→IDs."""
    hydrated: dict[str, list[dict[str, Any]]] = {}
    count = 0
    for section, ids_scores in sections.items():
        refs = []
        for item_id, score in ids_scores:
            item = ctx.get(item_id)
            if item is not None:
                r = item_to_result(item)
                r["score"] = score
                refs.append(r)
        if refs:
            hydrated[section] = refs
            count += len(refs)
    return {"sections": hydrated, "count": count}


# ---------------------------------------------------------------------------
# Extraction helpers (result dict -> IDs+scores)
# ---------------------------------------------------------------------------

def _extract_ids_scores(result: dict[str, Any]) -> list[tuple[str, float | None]]:
    """Extract [(id, score)] from a find action result."""
    return [
        (r["id"], r.get("score"))
        for r in result.get("results", [])
        if "id" in r
    ]


def _extract_meta_sections(
    result: dict[str, Any],
) -> dict[str, list[tuple[str, float | None]]]:
    """Extract {section: [(id, score)]} from resolve_meta result."""
    out: dict[str, list[tuple[str, float | None]]] = {}
    for section, refs in result.get("sections", {}).items():
        out[section] = [
            (r["id"], r.get("score"))
            for r in refs
            if "id" in r
        ]
    return out


# ---------------------------------------------------------------------------
# Cache entries
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _SimilarEntry:
    ids_scores: list[tuple[str, float | None]]
    item_id: str  # the similar_to value
    generation: int
    created_at: float  # monotonic


@dataclass(slots=True)
class _MetaEntry:
    sections: dict[str, list[tuple[str, float | None]]]
    item_id: str
    generation: int


@dataclass(slots=True)
class _PartsEntry:
    result: dict[str, Any]  # full result (parts aren't regular items)
    item_id: str  # base item


# ---------------------------------------------------------------------------
# SimilarCache
# ---------------------------------------------------------------------------

class SimilarCache:
    """Cache for find(similar_to=...) results.  Generation + TTL."""

    def __init__(self, *, max_entries: int = 500, ttl: float = 60.0) -> None:
        self._entries: OrderedDict[str, _SimilarEntry] = OrderedDict()
        self._generation: int = 0
        self._ttl = ttl
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> list[tuple[str, float | None]] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            now = time.monotonic()
            if entry.generation == self._generation:
                # Fresh — move to end (most recently used)
                self._entries.move_to_end(key)
                self.hits += 1
                return entry.ids_scores
            # Stale generation — serve if within TTL
            if (now - entry.created_at) < self._ttl:
                self._entries.move_to_end(key)
                self.hits += 1
                return entry.ids_scores
            # Expired
            del self._entries[key]
            self.misses += 1
            return None

    def put(
        self, key: str, ids_scores: list[tuple[str, float | None]], item_id: str,
    ) -> None:
        if not key:
            return
        with self._lock:
            self._entries[key] = _SimilarEntry(
                ids_scores=ids_scores,
                item_id=item_id,
                generation=self._generation,
                created_at=time.monotonic(),
            )
            self._entries.move_to_end(key)
            self._evict_lru()

    def on_write(self, item_id: str, tags: dict[str, Any]) -> int:
        with self._lock:
            self._generation += 1
            # Direct eviction for the written item
            evicted = 0
            to_del = [k for k, e in self._entries.items() if e.item_id == item_id]
            for k in to_del:
                del self._entries[k]
                evicted += 1
            return evicted

    def on_delete(self, item_id: str) -> int:
        return self.on_write(item_id, {})

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation = 0

    def _evict_lru(self) -> None:
        """Remove oldest entries if over capacity.  Caller holds lock."""
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


# ---------------------------------------------------------------------------
# MetaCache
# ---------------------------------------------------------------------------

class MetaCache:
    """Cache for resolve_meta results.  Generation-based (v1)."""

    def __init__(self, *, max_entries: int = 500) -> None:
        self._entries: OrderedDict[str, _MetaEntry] = OrderedDict()
        self._generation: int = 0
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict[str, list[tuple[str, float | None]]] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.generation == self._generation:
                self._entries.move_to_end(key)
                self.hits += 1
                return entry.sections
            # Stale — no TTL grace for meta, just evict
            del self._entries[key]
            self.misses += 1
            return None

    def put(
        self,
        key: str,
        sections: dict[str, list[tuple[str, float | None]]],
        item_id: str,
    ) -> None:
        if not key:
            return
        with self._lock:
            self._entries[key] = _MetaEntry(
                sections=sections,
                item_id=item_id,
                generation=self._generation,
            )
            self._entries.move_to_end(key)
            self._evict_lru()

    def on_write(self, item_id: str, tags: dict[str, Any]) -> int:
        with self._lock:
            self._generation += 1
            evicted = 0
            to_del = [k for k, e in self._entries.items() if e.item_id == item_id]
            for k in to_del:
                del self._entries[k]
                evicted += 1
            return evicted

    def on_delete(self, item_id: str) -> int:
        return self.on_write(item_id, {})

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation = 0

    def _evict_lru(self) -> None:
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


# ---------------------------------------------------------------------------
# PartsCache
# ---------------------------------------------------------------------------

class PartsCache:
    """Cache for find(prefix=...@p) results.  Item-based invalidation.

    Stores full result dicts because parts aren't regular items and
    can't be hydrated via ctx.get().
    """

    def __init__(self, *, max_entries: int = 500) -> None:
        self._entries: OrderedDict[str, _PartsEntry] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry.result

    def put(self, key: str, result: dict[str, Any], item_id: str) -> None:
        if not key:
            return
        with self._lock:
            self._entries[key] = _PartsEntry(
                result=result, item_id=item_id,
            )
            self._entries.move_to_end(key)
            self._evict_lru()

    def on_write(self, item_id: str, tags: dict[str, Any]) -> int:
        with self._lock:
            base = item_id.split("@p")[0] if is_part_id(item_id) else item_id
            evicted = 0
            to_del = [k for k, e in self._entries.items() if e.item_id == base]
            for k in to_del:
                del self._entries[k]
                evicted += 1
            return evicted

    def on_delete(self, item_id: str) -> int:
        return self.on_write(item_id, {})

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict_lru(self) -> None:
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


# ---------------------------------------------------------------------------
# ContextCache orchestrator
# ---------------------------------------------------------------------------

class ContextCache:
    """Routes cache operations to the appropriate sub-cache."""

    def __init__(
        self,
        *,
        similar_max: int = 500,
        similar_ttl: float = 60.0,
        meta_max: int = 500,
        parts_max: int = 500,
    ) -> None:
        self.similar = SimilarCache(max_entries=similar_max, ttl=similar_ttl)
        self.meta = MetaCache(max_entries=meta_max)
        self.parts = PartsCache(max_entries=parts_max)

    def check(
        self, action_name: str, params: dict[str, Any], ctx: Any,
    ) -> dict[str, Any] | None:
        """Check cache.  Returns hydrated result dict or None."""
        key = _cache_key(action_name, params)
        if not key:
            return None

        if action_name == _ACTION_FIND and params.get("similar_to"):
            ids_scores = self.similar.get(key)
            if ids_scores is None:
                return None
            return _hydrate_find_results(ids_scores, ctx)

        if action_name == _ACTION_RESOLVE_META:
            sections = self.meta.get(key)
            if sections is None:
                return None
            return _hydrate_meta_results(sections, ctx)

        if action_name == _ACTION_FIND and str(params.get("prefix", "")).endswith("@p"):
            return self.parts.get(key)

        return None

    def store(
        self, action_name: str, params: dict[str, Any], result: dict[str, Any],
    ) -> None:
        """Extract IDs+scores and store in the appropriate cache."""
        key = _cache_key(action_name, params)
        if not key:
            return

        if action_name == _ACTION_FIND and params.get("similar_to"):
            self.similar.put(
                key,
                _extract_ids_scores(result),
                item_id=str(params["similar_to"]),
            )
        elif action_name == _ACTION_RESOLVE_META:
            self.meta.put(
                key,
                _extract_meta_sections(result),
                item_id=str(params.get("item_id", "")),
            )
        elif action_name == _ACTION_FIND and str(params.get("prefix", "")).endswith("@p"):
            prefix = str(params["prefix"])
            base_id = prefix[:-2] if prefix.endswith("@p") else prefix
            self.parts.put(key, result, item_id=base_id)

    def notify_write(self, item_id: str, tags: dict[str, Any]) -> None:
        self.similar.on_write(item_id, tags)
        self.meta.on_write(item_id, tags)
        self.parts.on_write(item_id, tags)

    def notify_delete(self, item_id: str) -> None:
        self.similar.on_delete(item_id)
        self.meta.on_delete(item_id)
        self.parts.on_delete(item_id)

    def clear(self) -> None:
        self.similar.clear()
        self.meta.clear()
        self.parts.clear()

    def stats(self) -> dict[str, dict[str, int]]:
        return {
            "similar": {"hits": self.similar.hits, "misses": self.similar.misses},
            "meta": {"hits": self.meta.hits, "misses": self.meta.misses},
            "parts": {"hits": self.parts.hits, "misses": self.parts.misses},
        }
