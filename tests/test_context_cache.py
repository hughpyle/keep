"""Tests for context component cache."""

from __future__ import annotations

import time

import pytest

from keep.context_cache import (
    ContextCache,
    MetaCache,
    PartsCache,
    SimilarCache,
    _cache_key,
    _extract_ids_scores,
    _extract_meta_sections,
    _hydrate_find_results,
    _hydrate_meta_results,
)


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------

class TestCacheKey:
    """Tests for cache key generation."""
    def test_deterministic(self):
        k1 = _cache_key("find", {"similar_to": "abc", "limit": 3})
        k2 = _cache_key("find", {"similar_to": "abc", "limit": 3})
        assert k1 == k2
        assert len(k1) == 16

    def test_param_order_irrelevant(self):
        k1 = _cache_key("find", {"limit": 3, "similar_to": "abc"})
        k2 = _cache_key("find", {"similar_to": "abc", "limit": 3})
        assert k1 == k2

    def test_different_action_different_key(self):
        k1 = _cache_key("find", {"limit": 3})
        k2 = _cache_key("resolve_meta", {"limit": 3})
        assert k1 != k2

    def test_different_params_different_key(self):
        k1 = _cache_key("find", {"similar_to": "abc"})
        k2 = _cache_key("find", {"similar_to": "def"})
        assert k1 != k2

    def test_empty_params(self):
        k = _cache_key("find", {})
        assert len(k) == 16

    def test_non_serializable_returns_empty(self):
        k = _cache_key("find", {"obj": object()})
        # default=str handles this, so key should still be valid
        assert len(k) == 16


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

class TestExtraction:
    """Tests for ID and score extraction."""
    def test_extract_ids_scores(self):
        result = {
            "results": [
                {"id": "a", "summary": "x", "tags": {}, "score": 0.9},
                {"id": "b", "summary": "y", "tags": {}, "score": 0.8},
            ],
            "count": 2,
        }
        ids = _extract_ids_scores(result)
        assert ids == [("a", 0.9), ("b", 0.8)]

    def test_extract_ids_scores_none_score(self):
        result = {"results": [{"id": "a", "summary": "x"}], "count": 1}
        ids = _extract_ids_scores(result)
        assert ids == [("a", None)]

    def test_extract_meta_sections(self):
        result = {
            "sections": {
                "todo": [{"id": "t1", "score": 0.7}, {"id": "t2", "score": 0.6}],
                "learnings": [{"id": "l1", "score": 0.5}],
            },
            "count": 3,
        }
        sections = _extract_meta_sections(result)
        assert sections == {
            "todo": [("t1", 0.7), ("t2", 0.6)],
            "learnings": [("l1", 0.5)],
        }


# ---------------------------------------------------------------------------
# Hydration helpers
# ---------------------------------------------------------------------------

class _FakeItem:
    def __init__(self, id, summary="", tags=None):
        self.id = id
        self.summary = summary
        self.tags = tags or {}


class _FakeCtx:
    def __init__(self, items: dict[str, _FakeItem]):
        self._items = items

    def get(self, id):
        return self._items.get(id)

    def peek(self, id):
        return self._items.get(id)


class TestHydration:
    """Tests for find result hydration."""
    def test_hydrate_find_results(self):
        ctx = _FakeCtx({
            "a": _FakeItem("a", "summary a", {"tag": "1"}),
            "b": _FakeItem("b", "summary b"),
        })
        result = _hydrate_find_results([("a", 0.9), ("b", 0.8)], ctx)
        assert result["count"] == 2
        assert result["results"][0]["id"] == "a"
        assert result["results"][0]["summary"] == "summary a"
        assert result["results"][0]["score"] == 0.9
        assert result["results"][0]["tags"] == {"tag": "1"}

    def test_hydrate_find_skips_missing(self):
        ctx = _FakeCtx({"a": _FakeItem("a", "ok")})
        result = _hydrate_find_results([("a", 0.9), ("gone", 0.8)], ctx)
        assert result["count"] == 1
        assert result["results"][0]["id"] == "a"

    def test_hydrate_meta_results(self):
        ctx = _FakeCtx({
            "t1": _FakeItem("t1", "todo 1"),
            "l1": _FakeItem("l1", "learning 1"),
        })
        sections = {
            "todo": [("t1", 0.7)],
            "learnings": [("l1", 0.5)],
        }
        result = _hydrate_meta_results(sections, ctx)
        assert result["count"] == 2
        assert "todo" in result["sections"]
        assert result["sections"]["todo"][0]["summary"] == "todo 1"

    def test_hydrate_meta_drops_empty_sections(self):
        ctx = _FakeCtx({})  # all items gone
        sections = {"todo": [("gone", 0.7)]}
        result = _hydrate_meta_results(sections, ctx)
        assert result["count"] == 0
        assert result["sections"] == {}


# ---------------------------------------------------------------------------
# SimilarCache
# ---------------------------------------------------------------------------

class TestSimilarCache:
    """Tests for similar-results cache."""
    def test_put_and_get(self):
        c = SimilarCache()
        c.put("k1", [("a", 0.9)], item_id="x")
        assert c.get("k1") == [("a", 0.9)]
        assert c.hits == 1
        assert c.misses == 0

    def test_miss(self):
        c = SimilarCache()
        assert c.get("nonexistent") is None
        assert c.misses == 1

    def test_generation_invalidation_within_ttl(self):
        c = SimilarCache(ttl=60.0)
        c.put("k1", [("a", 0.9)], item_id="x")
        c.on_write("other", {})  # bumps generation
        # Still served within TTL (bounded staleness)
        assert c.get("k1") == [("a", 0.9)]

    def test_direct_eviction(self):
        c = SimilarCache()
        c.put("k1", [("a", 0.9)], item_id="target")
        c.on_write("target", {})  # evicts k1 directly
        assert c.get("k1") is None

    def test_ttl_expiry(self):
        c = SimilarCache(ttl=0.01)  # very short TTL
        c.put("k1", [("a", 0.9)], item_id="x")
        c.on_write("other", {})  # bumps generation
        time.sleep(0.02)
        assert c.get("k1") is None  # TTL expired

    def test_fresh_entry_survives_generation_bump(self):
        c = SimilarCache(ttl=60.0)
        c.put("k1", [("a", 0.9)], item_id="x")
        c.on_write("other", {})
        # Entry is stale-generation but within TTL
        result = c.get("k1")
        assert result == [("a", 0.9)]
        assert c.hits == 1

    def test_lru_eviction(self):
        c = SimilarCache(max_entries=3)
        c.put("k1", [("a", 0.9)], item_id="x")
        c.put("k2", [("b", 0.8)], item_id="y")
        c.put("k3", [("c", 0.7)], item_id="z")
        c.put("k4", [("d", 0.6)], item_id="w")  # evicts k1
        assert c.get("k1") is None
        assert c.get("k2") is not None

    def test_on_delete(self):
        c = SimilarCache()
        c.put("k1", [("a", 0.9)], item_id="target")
        c.on_delete("target")
        assert c.get("k1") is None

    def test_clear(self):
        c = SimilarCache()
        c.put("k1", [("a", 0.9)], item_id="x")
        c.clear()
        assert c.get("k1") is None

    def test_empty_key_skipped(self):
        c = SimilarCache()
        c.put("", [("a", 0.9)], item_id="x")
        assert c.get("") is None


# ---------------------------------------------------------------------------
# MetaCache
# ---------------------------------------------------------------------------

class TestMetaCache:
    """Tests for metadata cache."""
    def test_put_and_get(self):
        c = MetaCache()
        sections = {"todo": [("t1", 0.7)]}
        c.put("k1", sections, item_id="x")
        assert c.get("k1") == sections

    def test_miss(self):
        c = MetaCache()
        assert c.get("nope") is None

    def test_direct_eviction(self):
        c = MetaCache()
        c.put("k1", {"todo": [("t1", 0.7)]}, item_id="target")
        c.on_write("target", {})
        assert c.get("k1") is None

    def test_generation_eviction(self):
        c = MetaCache()
        c.put("k1", {"todo": [("t1", 0.7)]}, item_id="x")
        c.on_write("other", {"act": "request"})  # bumps generation
        # MetaCache has no TTL grace — stale entries are evicted
        assert c.get("k1") is None

    def test_lru_eviction(self):
        c = MetaCache(max_entries=2)
        c.put("k1", {"a": []}, item_id="x")
        c.put("k2", {"b": []}, item_id="y")
        c.put("k3", {"c": []}, item_id="z")  # evicts k1
        assert c.get("k1") is None
        assert c.get("k2") is not None

    def test_on_delete(self):
        c = MetaCache()
        c.put("k1", {"todo": [("t1", 0.7)]}, item_id="target")
        c.on_delete("target")
        assert c.get("k1") is None

    def test_clear(self):
        c = MetaCache()
        c.put("k1", {"todo": [("t1", 0.7)]}, item_id="x")
        c.clear()
        assert c.get("k1") is None


# ---------------------------------------------------------------------------
# PartsCache
# ---------------------------------------------------------------------------

class TestPartsCache:
    """Tests for parts cache."""
    def test_put_and_get(self):
        c = PartsCache()
        result = {"results": [{"id": "x@p1"}], "count": 1}
        c.put("k1", result, item_id="x")
        assert c.get("k1") == result

    def test_miss(self):
        c = PartsCache()
        assert c.get("nope") is None

    def test_evict_on_base_write(self):
        c = PartsCache()
        c.put("k1", {"results": [], "count": 0}, item_id="base")
        c.on_write("base", {})
        assert c.get("k1") is None

    def test_evict_on_part_write(self):
        c = PartsCache()
        c.put("k1", {"results": [], "count": 0}, item_id="base")
        c.on_write("base@p1", {})  # writing a part invalidates base
        assert c.get("k1") is None

    def test_unrelated_write_no_eviction(self):
        c = PartsCache()
        c.put("k1", {"results": [], "count": 0}, item_id="base")
        c.on_write("other", {})
        assert c.get("k1") is not None

    def test_lru_eviction(self):
        c = PartsCache(max_entries=2)
        c.put("k1", {"results": [], "count": 0}, item_id="a")
        c.put("k2", {"results": [], "count": 0}, item_id="b")
        c.put("k3", {"results": [], "count": 0}, item_id="c")  # evicts k1
        assert c.get("k1") is None
        assert c.get("k2") is not None

    def test_on_delete(self):
        c = PartsCache()
        c.put("k1", {"results": [], "count": 0}, item_id="base")
        c.on_delete("base")
        assert c.get("k1") is None


# ---------------------------------------------------------------------------
# ContextCache (orchestrator)
# ---------------------------------------------------------------------------

class TestContextCache:
    """Tests for context cache integration."""
    def _ctx(self, items=None):
        return _FakeCtx(items or {})

    def test_routing_similar(self):
        cc = ContextCache()
        result = {"results": [{"id": "a", "score": 0.9}], "count": 1}
        cc.store("find", {"similar_to": "x", "limit": 3}, result)
        ctx = self._ctx({"a": _FakeItem("a", "sum")})
        hydrated = cc.check("find", {"similar_to": "x", "limit": 3}, ctx)
        assert hydrated is not None
        assert hydrated["results"][0]["id"] == "a"

    def test_routing_meta(self):
        cc = ContextCache()
        result = {
            "sections": {"todo": [{"id": "t1", "score": 0.7}]},
            "count": 1,
        }
        cc.store("resolve_meta", {"item_id": "x", "limit": 3}, result)
        ctx = self._ctx({"t1": _FakeItem("t1", "task")})
        hydrated = cc.check("resolve_meta", {"item_id": "x", "limit": 3}, ctx)
        assert hydrated is not None
        assert hydrated["sections"]["todo"][0]["id"] == "t1"

    def test_routing_parts(self):
        cc = ContextCache()
        result = {"results": [{"id": "x@p1"}], "count": 1}
        cc.store("find", {"prefix": "x@p", "limit": 10}, result)
        hydrated = cc.check("find", {"prefix": "x@p", "limit": 10}, self._ctx())
        assert hydrated == result  # parts stored in full

    def test_routing_uncacheable(self):
        cc = ContextCache()
        # Regular find (no similar_to, no @p prefix) — not cached
        assert cc.check("find", {"query": "test"}, self._ctx()) is None
        # Unknown action
        assert cc.check("summarize", {}, self._ctx()) is None

    def test_notify_write_propagates(self):
        cc = ContextCache()
        # Store in all three caches
        cc.store("find", {"similar_to": "x", "limit": 3},
                 {"results": [{"id": "a", "score": 0.9}], "count": 1})
        cc.store("resolve_meta", {"item_id": "x", "limit": 3},
                 {"sections": {"s": [{"id": "b", "score": 0.5}]}, "count": 1})
        cc.store("find", {"prefix": "x@p", "limit": 10},
                 {"results": [{"id": "x@p1"}], "count": 1})

        cc.notify_write("x", {"act": "test"})

        ctx = self._ctx({"a": _FakeItem("a"), "b": _FakeItem("b")})
        # Similar: item_id="x" directly evicted
        assert cc.check("find", {"similar_to": "x", "limit": 3}, ctx) is None
        # Meta: item_id="x" directly evicted
        assert cc.check("resolve_meta", {"item_id": "x", "limit": 3}, ctx) is None
        # Parts: item_id="x" directly evicted
        assert cc.check("find", {"prefix": "x@p", "limit": 10}, ctx) is None

    def test_notify_delete_propagates(self):
        cc = ContextCache()
        cc.store("find", {"similar_to": "x", "limit": 3},
                 {"results": [{"id": "a", "score": 0.9}], "count": 1})
        cc.notify_delete("x")
        ctx = self._ctx({"a": _FakeItem("a")})
        assert cc.check("find", {"similar_to": "x", "limit": 3}, ctx) is None

    def test_stats(self):
        cc = ContextCache()
        cc.store("find", {"similar_to": "x", "limit": 3},
                 {"results": [{"id": "a", "score": 0.9}], "count": 1})
        ctx = self._ctx({"a": _FakeItem("a")})
        cc.check("find", {"similar_to": "x", "limit": 3}, ctx)  # hit
        cc.check("find", {"similar_to": "y", "limit": 3}, ctx)  # miss
        s = cc.stats()
        assert s["similar"]["hits"] == 1
        assert s["similar"]["misses"] == 1

    def test_clear(self):
        cc = ContextCache()
        cc.store("find", {"similar_to": "x", "limit": 3},
                 {"results": [{"id": "a", "score": 0.9}], "count": 1})
        cc.clear()
        ctx = self._ctx({"a": _FakeItem("a")})
        assert cc.check("find", {"similar_to": "x", "limit": 3}, ctx) is None

    def test_similar_generation_does_not_affect_parts(self):
        """Parts cache is unaffected by writes to unrelated items."""
        cc = ContextCache()
        cc.store("find", {"prefix": "base@p", "limit": 10},
                 {"results": [{"id": "base@p1"}], "count": 1})
        cc.notify_write("unrelated", {"act": "test"})
        result = cc.check("find", {"prefix": "base@p", "limit": 10}, self._ctx())
        assert result is not None  # parts not evicted
