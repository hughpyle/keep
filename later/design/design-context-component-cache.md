# Context Component Cache

## Problem

`get_context()` is the most expensive read path. Benchmark (10 runs, "now"):

```
item:      0.0ms    (SQLite lookup)
similar:  39ms      (vector search)
meta:    965ms      (meta-doc resolution — 87% of total)
edges:     0.1ms    (direct DB query)
parts:    30ms      (prefix query)
versions:  0.3ms    (version list)
full:   1104ms      (dominated by meta)
```

For larger items (thin_cli.py, 5 runs): similar 249ms, meta 2475ms.

Repeated access to the same item (common in agent workflows) repeats
all of this work. The daemon stays alive — an in-memory cache bounded
per component type can eliminate most recomputation.

## Design: Action-Level Cache in the Flow Engine

### Injection Point

Cache at the **action runner**, not above the flow. The flow engine
remains the single execution path — cached rules just resolve instantly.

```python
# In make_action_runner:
def _run(action_name, params):
    cached = cache.check(action_name, params)
    if cached is not None:
        return cached          # 0ms instead of 965ms
    act = get_action(action_name)
    result = act.run(params, ctx)
    cache.store(action_name, params, result)
    return result
```

Benefits:
- Flow history, bindings, tick count remain accurate
- Cache doesn't bypass flow structure or traceability
- Perf stats can annotate cache hit/miss per rule
- Cache operates at action level, not context level

### What's Cached

**IDs and scores, not full Items.** The cache stores result references:
- `find` results: `[(item_id, score), ...]`
- `resolve_meta` results: `{section: [item_id, ...]}`

Item data (summary, tags) is fetched fresh on hydration. This means
tag updates and summary changes appear immediately without cache
invalidation. The cache only tracks *which items are relevant*.

### Cache Key

The action params themselves form the cache key:

```python
def _cache_key(action_name: str, params: dict) -> str:
    canonical = json.dumps(
        {"a": action_name, **{k: v for k, v in sorted(params.items())}},
        sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

### Component Managers

Three managers with different invalidation strategies, one shared protocol:

```python
class ComponentCache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, result: dict, deps: CacheDeps) -> None: ...
    def on_write(self, item_id: str, tags: dict) -> int: ...  # returns eviction count
    def on_delete(self, item_id: str) -> int: ...
```

#### SimilarCache (generation + TTL)

- **Cache key**: `(similar_to=item_id, tags, limit)`
- **Invalidation**: On any write, bump generation counter. Cached entries
  with old generation are served if TTL hasn't expired (bounded staleness).
  Entry for the written item itself is invalidated immediately.
- **TTL**: 60s (tunable). After TTL, stale-generation entries are evicted
  on next access.
- **Bound**: 500 entries, LRU eviction.

```python
class SimilarCache:
    _entries: OrderedDict[str, SimilarEntry]
    _generation: int = 0
    _ttl: float = 60.0
    _max_entries: int = 500

    def on_write(self, item_id: str, tags: dict):
        # Evict entries FOR this item immediately
        # Bump generation for all others (lazy TTL invalidation)
        self._generation += 1
```

#### MetaCache (tag dependency graph)

- **Cache key**: `(item_id, meta_doc_name, limit)` — but effectively
  keyed by the resolved find params `(similar_to, tags, limit)`
- **Dependency graph**: Built once from meta-doc YAML at startup.
  Maps `(tag_key, tag_value) → {meta_section_names}`.

  ```
  .meta/todo queries:  act=commitment, act=request, status=open, ...
  .meta/learnings:     type=learning, type=breakdown, type=gotcha
  .meta/genre:         genre={params.genre}  (dynamic)
  ```

- **Invalidation**: On write, check if the written item's tags intersect
  any meta dependency. If `act=assertion` is written, invalidate all
  cached entries that include the `learnings` section (because learnings
  queries `type=learning` etc. — wait, that's a different tag).

  More precisely: invalidate entries where *any cached find rule's tag
  filter matches the written item's tags*. The cache stores the tag
  filter alongside each entry for this check.

- **Dynamic deps** (genre, album, artist): These depend on the *viewed*
  item's tags, not the written item. A write to an item with `genre=jazz`
  doesn't invalidate the cached meta for item X (which has its own genre
  tag). But if item X's tags change (via `tag(X, {genre: "rock"})`), then
  X's own cached meta is invalidated (direct eviction on write to X).

- **Bound**: 500 entries, LRU eviction.

```python
class MetaCache:
    _entries: OrderedDict[str, MetaEntry]
    _tag_index: dict[str, set[str]]  # tag_key → {cache_keys with that dep}
    _max_entries: int = 500

    def _build_deps(self, meta_docs):
        """Parse meta-doc YAML, extract tag filters from find rules."""

    def on_write(self, item_id: str, tags: dict):
        # 1. Evict this item's own cached meta
        # 2. Check tag index: do any cached entries have find rules
        #    whose tag filters match the written item's tags?
        for tag_key, tag_value in tags.items():
            if tag_key in self._tag_index:
                # Evict affected entries
```

#### PartsCache (content hash)

- **Cache key**: `(item_id, limit)` — actually just the find prefix query
- **Invalidation**: On write to the item (re-analyze changes parts).
  Specifically, when `_analyzed_hash` changes.
- **Bound**: 500 entries, LRU eviction.

### ContextCache Orchestrator

```python
class ContextCache:
    similar: SimilarCache
    meta: MetaCache
    parts: PartsCache

    def check(self, action_name: str, params: dict) -> dict | None:
        """Check all component caches for this action call."""
        key = _cache_key(action_name, params)
        # Route to appropriate cache based on action + params
        if action_name == "find" and params.get("similar_to"):
            return self.similar.get(key)
        if action_name == "resolve_meta":
            return self.meta.get(key)
        if action_name == "find" and params.get("prefix", "").endswith("@p"):
            return self.parts.get(key)
        return None  # not cacheable

    def store(self, action_name: str, params: dict, result: dict):
        key = _cache_key(action_name, params)
        deps = self._extract_deps(action_name, params)
        if action_name == "find" and params.get("similar_to"):
            self.similar.put(key, result, deps)
        elif action_name == "resolve_meta":
            self.meta.put(key, result, deps)
        elif action_name == "find" and params.get("prefix", "").endswith("@p"):
            self.parts.put(key, result, deps)

    def notify_write(self, item_id: str, tags: dict):
        self.similar.on_write(item_id, tags)
        self.meta.on_write(item_id, tags)
        self.parts.on_write(item_id, tags)

    def notify_delete(self, item_id: str):
        self.similar.on_delete(item_id)
        self.meta.on_delete(item_id)
        self.parts.on_delete(item_id)
```

### Integration with Keeper

```python
# Keeper.__init__:
self._context_cache = ContextCache() if not remote else None

# Keeper._upsert (after successful write):
if self._context_cache:
    self._context_cache.notify_write(id, merged_tags)

# Keeper.delete:
if self._context_cache:
    self._context_cache.notify_delete(id)
```

### Integration with Action Runner

```python
# In state_doc_runtime.py make_action_runner:
def _run(action_name, params):
    cache = getattr(ctx, '_context_cache', None)
    if cache:
        cached = cache.check(action_name, params)
        if cached is not None:
            perf.record(action_name, "cache_hit")
            return cached
    act = get_action(action_name)
    result = act.run(params, ctx)
    if cache:
        cache.store(action_name, params, result)
    return result
```

## Performance Targets

With cache warm (second access to same item):

| Component | Before | After (target) | Speedup |
|-----------|--------|-----------------|---------|
| similar | 39ms | <1ms | 40x |
| meta | 965ms | <1ms | 1000x |
| parts | 30ms | <1ms | 30x |
| full context | 1104ms | ~5ms | 200x |

## Implementation Order

1. **ContextCache + SimilarCache** — simplest invalidation (generation+TTL),
   immediate benchmark improvement for `similar` component
2. **MetaCache** — biggest absolute improvement (965ms → <1ms), requires
   tag dependency graph extraction from meta-doc YAML
3. **PartsCache** — modest improvement, straightforward invalidation
4. **Perf instrumentation** — annotate cache hit/miss in perf_stats,
   add cache stats to daemon health endpoint
5. **Benchmark comparison** — re-run bench/context_perf.py before/after

## Open Questions

1. **Should the cache survive daemon restart?** Currently no — in-memory
   only. The cache warms quickly (first access to each item). Persisting
   to SQLite adds complexity for marginal benefit.

2. **Cache size tuning.** 500 entries per component = 1500 total. At
   ~100 bytes per entry (IDs + scores), that's ~150KB. Negligible.
   Could go to 5000 without concern.

3. **resolve_meta action vs find action.** The meta flow runs multiple
   `find` actions (one per meta-doc rule). Should we cache at the
   individual `find` level or at the `resolve_meta` level? Individual
   find caching is more granular (a single meta section invalidation
   doesn't trash the whole meta result), but resolve_meta caching is
   simpler. Start with find-level caching.

4. **Flow writable flag.** Read-only flows (writable=False) are safe
   to cache. Writable flows may have side effects — should we skip
   caching for those? The get-context flow is read-only, so this is
   academic for now.
