# Unified Get + Query Cache

## Problem

Three related issues:

**1. `find` and `get` are conceptually the same operation — resolution from a position.**

`get("abc123")` resolves an exact ID. `find("caching strategy")` resolves a semantic query. Both take a string, both return items. The difference is precision: one is exact coordinates, the other is a bearing. Currently they're separate API methods with different return types (`Optional[Item]` vs `list[Item]`), different code paths, and different call sites. An agent choosing between them must already know whether its string is an ID or a query — but that's the server's job.

**2. `get` is expensive for what it should be.**

`get_context()` assembles an `ItemContext` by running a state-doc flow that calls `find(similar_to=...)`, `resolve_meta()`, `list_parts()`, `list_versions()`, and edge resolution. For a single item, this is 5+ database operations including a vector search. Repeated access to the same item (common in agent workflows: get, think, update, get again) repeats all of this work.

**3. Queries leave no trace.**

When an agent searches for "caching strategy" and gets 5 results, that interaction vanishes. The agent found something, but the act of finding isn't recorded. If it searches again 10 minutes later, it repeats the full computation. If another agent (or the same agent in a new session) asks the same question, no prior work is reusable.

## Design

### Part 1: Unified `get` — merge find into get

**Core change:** `get(id_or_query, context=None)` becomes the single retrieval method.

```python
def get(
    self,
    id_or_query: str,
    *,
    context: str | None = None,    # "where I'm looking from"
    tags: TagMap | None = None,
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
    deep: bool = False,
    scope: str | None = None,
) -> GetResult:
```

**Resolution order:**

```
get("X") →
  1. Exact ID lookup: document_store.get(collection, normalize_id("X"))
     → found? return GetResult(item=item, source="exact")

  2. Query cache lookup: query_cache.get(cache_key("X", tags, limit))
     → valid hit? return GetResult(items=cached, source="cache")

  3. Semantic + FTS search: existing hybrid find() path
     → results found? cache them, return GetResult(items=results, source="search")

  4. Nothing found: return GetResult(items=[], source="search")
```

**`GetResult` replaces the current split:**

```python
@dataclass
class GetResult:
    """Unified result from get()."""
    items: list[Item]            # 1 item for exact match, N for search
    source: Literal["exact", "cache", "search"]
    cache_key: str | None = None # present when result was cached or is cacheable

    @property
    def item(self) -> Item | None:
        """Convenience: first item, or None."""
        return self.items[0] if self.items else None
```

**Backward compatibility:**

The current `get(id) -> Optional[Item]` and `find(query, ...) -> list[Item]` methods remain as thin wrappers:

```python
# Existing API preserved
def get(self, id: str) -> Optional[Item]:
    result = self._unified_get(id)
    return result.item if result.source == "exact" else None

def find(self, query=None, *, similar_to=None, **kw) -> list[Item]:
    if similar_to:
        # similar_to path unchanged (uses stored embedding, not query text)
        return self._find_similar(similar_to, **kw)
    return self._unified_get(query, **kw).items
```

The new unified method is `_unified_get()` internally, exposed as a new public method (name TBD: `resolve()`? `query()`? keep `get` and rename old get to `get_exact`?).

**Decision needed:** Public API naming. Options:
- `resolve(id_or_query)` — clearest but new name
- `get(id_or_query)` with `get_exact(id)` for the old behavior
- Keep both `get` and `find` as wrappers, add `resolve` as the unified method

**The `context` parameter:**

When provided, `context` is a second text-or-URI that influences result ranking. Not a filter — a relevance signal. Implementation:

```python
if context:
    context_embedding = self._embed(context)
    # Boost results that are close to both target AND context
    for item in results:
        item_embedding = self._get_embedding(item.id)
        context_score = cosine_similarity(item_embedding, context_embedding)
        item.score = item.score * (1 + 0.3 * context_score)  # tunable weight
    results.sort(key=lambda x: x.score, reverse=True)
```

This is the "where I'm looking from" discussed in conversation. Deferred to Phase 2 — the cache should work first.

---

### Part 2: Query Result Cache

#### Cache table (SQLite, in document_store)

```sql
CREATE TABLE IF NOT EXISTS query_cache (
    cache_key    TEXT PRIMARY KEY,   -- SHA256(canonical_query_params)
    query_text   TEXT NOT NULL,      -- original query (for debugging/display)
    result_ids   TEXT NOT NULL,      -- JSON array of [id, score] pairs
    result_count INTEGER NOT NULL,   -- len(results) for quick stats
    tags_json    TEXT,               -- tag filter used (NULL = none)
    params_json  TEXT NOT NULL,      -- full params for cache validation
    created_at   TEXT NOT NULL,      -- when cached
    epoch        REAL NOT NULL,      -- ChromaDB epoch mtime at cache time
    hit_count    INTEGER DEFAULT 0,  -- access counter for LRU
    last_hit_at  TEXT                -- last access time
);

CREATE INDEX IF NOT EXISTS idx_query_cache_epoch
ON query_cache (epoch);
```

Schema version: 13 (bump from current 12).

#### Cache key computation

```python
def _query_cache_key(
    query: str,
    tags: TagMap | None,
    limit: int,
    scope: str | None,
    since: str | None,
    until: str | None,
) -> str:
    """Deterministic cache key from query parameters."""
    canonical = json.dumps({
        "q": query,
        "t": normalize_tag_map(tags) if tags else None,
        "l": limit,
        "s": scope,
        "si": since,
        "un": until,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
```

Key properties:
- Deterministic: same params → same key
- Tag-normalized: casefold + sorted → no false misses from ordering
- Includes limit: `limit=5` and `limit=10` are separate cache entries
- Does NOT include `context` (context affects ranking, not the result set; cached results can be re-ranked with context at read time)

#### Cache write (on search completion)

```python
def _cache_query_result(self, cache_key: str, query: str,
                         results: list[Item], tags: TagMap | None,
                         params: dict) -> None:
    """Store search results in the query cache."""
    result_pairs = [[item.id, item.score] for item in results]
    epoch = self._store._read_epoch()  # current ChromaDB epoch

    self._document_store._execute("""
        INSERT OR REPLACE INTO query_cache
        (cache_key, query_text, result_ids, result_count, tags_json,
         params_json, created_at, epoch, hit_count, last_hit_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
    """, (
        cache_key,
        query,
        json.dumps(result_pairs),
        len(result_pairs),
        json.dumps(normalize_tag_map(tags)) if tags else None,
        json.dumps(params, sort_keys=True),
        utc_now(),
        epoch,
    ))
```

#### Cache read (on get/find)

```python
def _check_query_cache(self, cache_key: str) -> list[Item] | None:
    """Check cache. Returns items if valid, None if miss or stale."""
    row = self._document_store._execute(
        "SELECT result_ids, epoch FROM query_cache WHERE cache_key = ?",
        (cache_key,)
    ).fetchone()

    if row is None:
        return None  # cache miss

    cached_epoch = row["epoch"]
    current_epoch = self._store._read_epoch()

    if cached_epoch == current_epoch:
        # Fast path: nothing has changed since cache was written
        self._document_store._execute(
            "UPDATE query_cache SET hit_count = hit_count + 1, last_hit_at = ? WHERE cache_key = ?",
            (utc_now(), cache_key)
        )
        return self._hydrate_cached_results(row["result_ids"])

    # Epoch changed — cache is stale
    return None
```

#### Cache invalidation strategy

**Primary mechanism: epoch-based.**

The ChromaDB epoch sentinel (`.chroma.epoch` mtime) is bumped on every vector store write. The cache stores the epoch at write time. On read, if epoch matches → valid. If epoch changed → stale.

This is conservative: ANY write anywhere invalidates ALL cached queries. But it's correct, simple, and the epoch check is a single `stat()` call (~1μs).

**Why not finer-grained invalidation?**

Considered and rejected (for now):

| Strategy | Pro | Con |
|---|---|---|
| Per-item content_hash check | Only invalidates when results changed | Doesn't detect new items that SHOULD appear |
| Outbox-based (check mutations since cached epoch) | Only invalidates affected queries | Complex: which mutations affect which queries? Semantic similarity means any new item could affect any query |
| TTL-based | Simple, predictable | Arbitrary: too short = no benefit, too long = stale results |
| Embedding-distance threshold | Only invalidate if new items are "close enough" to the query | Requires computing similarity on every write — defeats the purpose |

The fundamental problem: **semantic search cache invalidation is hard because any new content could be relevant to any cached query.** Unlike an exact-match cache (where you can identify which cache entries a mutation affects), a vector similarity cache has no efficient way to determine "would this new embedding appear in the top-K for this query?" without re-running the query.

**Epoch-based is the right starting point.** Optimize later if profiling shows cache hit rates are too low due to frequent epoch bumps.

**Mitigation for low hit rates:**

Background workers (embedding, summarization) bump the epoch frequently. To prevent them from thrashing the cache:

1. **Separate read and write epochs.** The query cache tracks the *document store* epoch (a new counter), not the ChromaDB epoch. Document content changes (new items, updated summaries) invalidate the cache. Embedding-only updates (background worker adding vectors for existing items) do not — they don't change what items exist, only whether they're findable by vector search. A newly embedded item was already findable by FTS.

   Implementation: add a `_doc_epoch` counter in the document_store, bumped by `upsert()` and `delete()` only. The query cache keys off this, not `.chroma.epoch`.

2. **Grace period.** Cache entries less than N seconds old are considered valid regardless of epoch. This prevents rapid put-then-get cycles from always missing.

   ```python
   if cached_epoch != current_epoch:
       cache_age = (now - cached_created_at).total_seconds()
       if cache_age < CACHE_GRACE_SECONDS:  # e.g., 5 seconds
           return hydrated_results  # still valid, too fresh to be stale
   ```

#### Cache eviction

```python
MAX_CACHE_ENTRIES = 1000

def _evict_cache(self) -> None:
    """Evict oldest/least-used entries when cache exceeds max size."""
    count = self._document_store._execute(
        "SELECT COUNT(*) FROM query_cache"
    ).fetchone()[0]

    if count > MAX_CACHE_ENTRIES:
        # Delete least-recently-hit entries beyond the limit
        self._document_store._execute("""
            DELETE FROM query_cache WHERE cache_key IN (
                SELECT cache_key FROM query_cache
                ORDER BY
                    COALESCE(last_hit_at, created_at) ASC
                LIMIT ?
            )
        """, (count - MAX_CACHE_ENTRIES,))
```

Run eviction after every cache write (cheap — just a count + conditional delete).

---

### Part 3: Context Cache

`get_context()` is the most expensive read path: it assembles `ItemContext` with similar items, meta-docs, parts, versions, and edges. This is what the CLI `get` and the MCP tool both call.

#### Context cache table

```sql
CREATE TABLE IF NOT EXISTS context_cache (
    cache_key    TEXT PRIMARY KEY,   -- SHA256(id + version + params)
    item_id      TEXT NOT NULL,      -- the item this context is for
    context_json TEXT NOT NULL,      -- serialized ItemContext
    doc_epoch    INTEGER NOT NULL,   -- document epoch at cache time
    created_at   TEXT NOT NULL,
    hit_count    INTEGER DEFAULT 0,
    last_hit_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_context_cache_item
ON context_cache (item_id);
```

#### Cache key for context

```python
def _context_cache_key(
    id: str,
    version: int | None,
    similar_limit: int,
    meta_limit: int,
    parts_limit: int,
    edges_limit: int,
    versions_limit: int,
) -> str:
    canonical = json.dumps({
        "id": id,
        "v": version,
        "sl": similar_limit,
        "ml": meta_limit,
        "pl": parts_limit,
        "el": edges_limit,
        "vl": versions_limit,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
```

#### Integration with get_context()

```python
def get_context(self, id, *, version=None, **limits) -> ItemContext | None:
    cache_key = _context_cache_key(id, version, **limits)

    # Check cache
    cached = self._check_context_cache(cache_key)
    if cached is not None:
        return cached

    # Existing assembly logic (unchanged)
    context = self._assemble_context(id, version=version, **limits)
    if context is None:
        return None

    # Cache the result
    self._cache_context(cache_key, id, context)
    return context
```

#### Invalidation

Same doc_epoch mechanism as query cache. Additionally, context cache entries for a specific item are invalidated when that item is written:

```python
def _invalidate_context_for_item(self, item_id: str) -> None:
    """Clear context cache entries for a specific item."""
    self._document_store._execute(
        "DELETE FROM context_cache WHERE item_id = ?",
        (item_id,)
    )
```

Called from `_upsert()` after a successful write.

---

### Part 4: Query-as-Object (queries leave traces)

**Should every search also `put` itself?**

The argument: queries represent attention. "What was the agent looking for?" is metadata worth preserving. Storing queries as items means they participate in the graph — you can traverse from a query to its results, from a result back to the queries that found it.

**Recommendation: Yes, but gated and namespaced.**

Not every search should become an item. Criteria:

```python
QUERY_PERSIST_MIN_RESULTS = 1    # must have found something
QUERY_PERSIST_MIN_SCORE = 0.3    # top result must be above threshold

def _maybe_persist_query(self, query: str, results: list[Item],
                          tags: TagMap | None) -> str | None:
    """Persist a query as an item if it meets criteria. Returns item ID or None."""
    if not results or results[0].score < QUERY_PERSIST_MIN_SCORE:
        return None

    query_id = _text_content_id(query)  # %{sha256[:12]}

    # Check if already persisted (idempotent)
    existing = self._document_store.get(self._resolve_doc_collection(), query_id)
    if existing:
        # Update access count, don't re-persist
        return query_id

    # Persist as item with query tags
    self._upsert(
        id=query_id,
        content=query,
        system_tags={
            "_source": "query",
            "_content_type": "query",
        },
        tags=tags or {},
        queue_summarize=False,  # query text IS the summary
    )

    # Create edges: query → results
    doc_coll = self._resolve_doc_collection()
    for i, item in enumerate(results[:10]):  # cap at 10 edges
        self._document_store.upsert_edge(
            doc_coll,
            source_id=query_id,
            predicate="query_result",
            target_id=item.id,
            inverse="found_by",
        )

    return query_id
```

**Namespace isolation:**

Query items have `_source=query` and their IDs are content-hashes (`%...`). They're excluded from normal search results by default:

```python
# In find() / _unified_get():
if not include_queries:
    # Exclude query items from results
    where = _add_where_clause(where, {"_source": {"$ne": "query"}})
```

**What this enables:**

1. **"What have I searched for?"** — `find(tags={"_source": "query"})` returns all past queries
2. **"How did I find this?"** — `get_context(item_id)` shows `found_by` edges pointing to the queries
3. **"What else did that search find?"** — follow `query_result` edges from a query item
4. **Deduplication** — same query text → same content-hash → same item (idempotent via `_upsert`)
5. **Convergence with put** — `put("caching strategy")` and `get("caching strategy")` both end up creating `%{hash}`, one explicitly, one as a side effect of searching

**Phase this separately.** The cache provides the performance benefit immediately. Query-as-object is a semantic enrichment that can be added after the cache is proven.

---

## Implementation Plan

### Phase 1: Query Result Cache (schema v13)

**Files changed:**

| File | Changes |
|---|---|
| `keep/document_store.py` | Add `query_cache` table, migration v12→v13, cache read/write/evict methods, doc_epoch counter |
| `keep/api.py` | Add `_query_cache_key()`, `_check_query_cache()`, `_cache_query_result()` in find path |
| `keep/store.py` | Expose `_read_epoch()` as public (already nearly public) |
| `tests/test_query_cache.py` | New: cache hit/miss, invalidation, eviction, epoch semantics |

**Steps:**

1. **Add doc_epoch to document_store** (new column or PRAGMA, bumped on upsert/delete only)
2. **Create query_cache table** in migration v12→v13
3. **Implement cache_key computation** in api.py
4. **Wire cache check into find()** — check before search, write after search
5. **Add cache eviction** — run after writes, cap at 1000 entries
6. **Add grace period** — 5-second freshness window
7. **Tests:** cache hit, cache miss, invalidation on write, eviction, grace period

**Estimated scope:** ~200 lines of production code, ~300 lines of tests.

### Phase 2: Context Cache

**Files changed:**

| File | Changes |
|---|---|
| `keep/document_store.py` | Add `context_cache` table (same migration or v14) |
| `keep/_context_resolution.py` | Cache check/write around `get_context()` assembly |
| `keep/api.py` | Invalidate context cache on item write |
| `tests/test_context_cache.py` | New: context cache hit/miss, per-item invalidation |

**Steps:**

1. **Create context_cache table**
2. **Add ItemContext serialization** (to_dict/from_dict already exists via `ItemContext.from_dict`)
3. **Wire cache into get_context()** — check before assembly, write after
4. **Invalidate on write** — clear context_cache entries for the written item in _upsert
5. **Tests**

**Estimated scope:** ~150 lines of production code, ~200 lines of tests.

### Phase 3: Unified Get (API change)

**Files changed:**

| File | Changes |
|---|---|
| `keep/api.py` | New `_unified_get()` method, preserve `get`/`find` wrappers |
| `keep/protocol.py` | Add `resolve()` to protocol if exposing new public method |
| `keep/cli.py` | Update `get` command to try search on miss (with flag) |
| `keep/flow_env.py` | Update action context `get` to use unified path |
| `tests/test_unified_get.py` | New: exact→cache→search fallthrough, backward compat |

**Steps:**

1. **Implement `_unified_get()`** with the three-step resolution
2. **Preserve backward compat** — `get()` and `find()` wrappers call `_unified_get()`
3. **CLI integration** — `keep get "caching strategy"` falls through to search if no exact ID
4. **MCP/flow integration** — update action context to use unified path
5. **Tests**

**Estimated scope:** ~100 lines of production code, ~200 lines of tests. Mostly wiring.

### Phase 4: Query-as-Object (optional, later)

**Files changed:**

| File | Changes |
|---|---|
| `keep/api.py` | `_maybe_persist_query()`, edge creation, filter queries from results |
| `keep/data/system/` | New `.tag/query_result` and `.tag/found_by` system tag docs |
| `tests/test_query_persistence.py` | New: query creation, idempotency, edge traversal, filtering |

**Estimated scope:** ~150 lines of production code, ~200 lines of tests.

---

## Open Questions

### 1. Doc epoch: column or pragma?

Options:
- **SQLite PRAGMA (like user_version):** Single integer, atomic increment. But only one value per database — can't track per-collection epochs.
- **Counter in a `metadata` table:** `INSERT OR REPLACE INTO metadata (key, value) VALUES ('doc_epoch', doc_epoch + 1)`. More flexible, works per-collection if needed.
- **Reuse the existing `.chroma.epoch` file mtime:** Simpler (no new mechanism), but includes embedding-only writes.

Recommendation: New `doc_epoch` integer in a `metadata` KV table. Bumped by document upsert/delete triggers (alongside the existing outbox triggers). Read is a single `SELECT value FROM metadata WHERE key = 'doc_epoch'`.

### 2. Cache key: should embedding model be part of the key?

If the embedding model changes (e.g., switching from `all-MiniLM-L6-v2` to `nomic-embed-text`), cached results from the old model are meaningless. Options:
- Include model name in cache key (safe but invalidates everything on model change)
- Flush entire cache on model change (simpler, same effect)

Recommendation: Flush cache on model change. The embedding dimension check already detects this; add a cache flush there.

### 3. Should the context cache store serialized JSON or binary?

`ItemContext.from_dict()` already exists. JSON is human-debuggable but larger. Binary (pickle/msgpack) is smaller but opaque.

Recommendation: JSON. The context cache is bounded (one entry per item × param combination), and debuggability matters more than size in SQLite.

### 4. `similar_to` queries: cacheable?

`find(similar_to=item_id)` uses the item's stored embedding. If the item's content changes, its embedding changes, and cached results are stale. The doc_epoch mechanism handles this (the write that changed the item bumps the epoch), but there's a subtlety: the embedding update is deferred (background worker). Between the content write and the embedding update, the cached results reflect the OLD embedding.

Recommendation: Don't cache `similar_to` queries in Phase 1. They're less common and have trickier invalidation semantics. Add in Phase 2 with an additional check: cache key includes the item's content_hash, so a content change produces a different cache key.

### 5. How to handle `deep=True` results?

Deep search follows edges from primary results to discover bridge items. The result includes `FindResults.deep_groups`. Should the cache store deep groups?

Recommendation: Yes. The cache stores the full `FindResults` including deep_groups. The cache key includes `deep=True/False` so deep and non-deep results are separate entries.

### 6. Per-item context invalidation: cascade?

When item A is written, its context cache is cleared. But what about items that reference A (via edges, similar, meta)? Their contexts now show stale data about A.

Recommendation: Don't cascade. Item A's write bumps the doc_epoch, which invalidates all caches (including B's context). Per-item invalidation is an optimization on top of epoch-based invalidation, not a replacement. It ensures that `put(A); get_context(A)` never returns stale data for A itself, even within the grace period.
