# Deep Code Review: keep v0.27.3

**Reviewers:** Keeper + Claude Code (Opus 4.6)
**Date:** 2026-02-09
**Scope:** Every `.py` file in `keep/` and `tests/` — security, performance, consistency
**Files reviewed:** 24 source files, 14 test files

---

## Overall Assessment

Solid for a v0.27 personal-use tool. Well-structured with clean separation (providers, store, document_store, API, CLI). The dual-store architecture (SQLite canonical + ChromaDB embeddings) is thoughtful. No catastrophic issues.

---

## Findings

### 1. ~~LIKE Injection in `query_by_id_prefix`~~ FIXED
**File:** `document_store.py`, `query_by_id_prefix()`
**Status:** Already escaped with `ESCAPE '\\'` clause. Current callers pass fixed strings (`".meta/"`, `"_text:"`). No action needed.

### 2. ~~Frozen Dataclass Mutation in `_rank_by_relevance`~~ FIXED
**Severity:** HIGH (runtime crash)
**File:** `api.py`, `_rank_by_relevance()`
**Status:** Now creates new `Item` instances, matching `_apply_recency_decay` pattern.

### 3. ~~`update()` and `remember()` Duplication~~  FIXED
**File:** `api.py`
**Status:** Extracted to `_upsert()` private method. Both are now thin wrappers.

### 4. ~~Embedding Computed Before Change Detection~~ FIXED
**Severity:** MEDIUM (performance)
**File:** `api.py`, `_upsert()`
**Status:** Early return when content+tags unchanged. Reuses stored embedding via `get_embedding()` when only tags/summary change.

### 5. ~~Re-embedding Old Content on Version Archival~~ FIXED
**Severity:** MEDIUM (performance)
**File:** `api.py`, `_upsert()` version archival section
**Status:** Retrieves existing embedding from ChromaDB before overwriting. Falls back to re-embed only if not found.

### 6. ~~Error Log Written to `/tmp`~~ FIXED
**Severity:** LOW-MEDIUM (security)
**File:** `errors.py:11`
**Status:** Moved to `~/.keep/errors.log` with `mkdir` on write.

### 7. ~~Tag Queries Scan Entire Collection~~ FIXED
**Severity:** MEDIUM (performance)
**File:** `document_store.py`, `list_distinct_tag_keys()` / `list_distinct_tag_values()`
**Status:** Replaced Python-side JSON parsing with SQLite `json_each()` and `json_extract()` for server-side extraction.

### 8. ~~`touch()` and `touch_many()` Missing Lock~~ FIXED
**Severity:** LOW (consistency)
**File:** `document_store.py:396-415`
**Status:** Added `with self._lock:` to both methods.

### 9. CLI Global Mutable State
**Severity:** LOW-MEDIUM (consistency)
**File:** `cli.py:56-96`

Four module-level globals (`_json_output`, `_ids_output`, `_full_output`, `_store_override`) control output formatting. Not a problem for CLI usage, but makes code non-reentrant and test-unfriendly.

**Fix (future):** Pass format options through Typer context.

### 10. Migration Bypasses Store Encapsulation
**Severity:** MEDIUM (consistency)
**File:** `api.py:546-565`

`_migrate_system_documents` reaches into `DocumentStore._conn` for raw SQL and `ChromaStore._get_collection()` for direct ChromaDB access, bypassing public APIs.

**Fix (future):** Add `DocumentStore.migrate_id(old_id, new_id, preserve_timestamps=True)`.

### 11. Broad Exception Swallowing in Migration
**Severity:** LOW-MEDIUM (correctness)
**Files:** `api.py:517,533,566,571,582`, `store.py:267`

Six bare `except Exception` handlers in `_migrate_system_documents` silently swallow errors. `store.py:267` catches `Exception` for a ChromaDB metadata filter that should catch `ValueError`.

**Fix (future):** Narrow to specific exception types. Log at `logger.debug()` minimum.

### 12. ~~Non-atomic Dual-Store Writes~~ FIXED
**Severity:** MEDIUM (consistency)
**File:** `api.py`, `__init__()` / `_upsert()` / `reconcile()`
**Status:** Startup consistency check compares ID sets (cheap). If diverged, auto-reconcile runs on first write when embedding provider is available. `reconcile()` also fixed to embed summaries instead of re-fetching (works for inline content), and now cleans up orphaned ChromaDB entries.

### 13. Unbounded Version History Growth
**Severity:** LOW (performance)
**File:** `document_store.py:296-338`

Every `upsert()` archives with no upper bound. Frequently-updated documents accumulate unbounded versions.

**(Hugh: don't fix, this is ok for a while)**

### 14. ~~`_gather_context` Double Embedding~~ FIXED
**Severity:** LOW (performance)
**File:** `api.py`, `find_similar()`
**Status:** `find_similar` now retrieves stored embedding from ChromaDB via `get_embedding()` instead of re-embedding. Falls back to `embed()` only if not found.

### 15. HTTP Document Provider Follows Redirects
**Severity:** LOW (security)
**File:** `providers/documents.py:182-232`

`requests.get` follows redirects by default. SSRF risk if exposed as service. Low impact for personal CLI.

### 16. YAML Frontmatter Deserialization
**Severity:** LOW (security)
**File:** `api.py`, `_load_frontmatter()`

`yaml.safe_load` is used (good). Arbitrary tag key/value pairs end up in store, but system tag prefix filtering prevents overwriting system tags. Acceptable.

### 17. `dequeue` Increments Attempts Before Processing
**Severity:** LOW (correctness)
**File:** `pending_summaries.py`

Attempt counter is incremented on dequeue, not after processing. Crash between dequeue and processing causes premature abandonment at `MAX_SUMMARY_ATTEMPTS`.

### 18. Embedding Cache `_maybe_evict` Double-Lock
**Severity:** LOW (style)
**File:** `embedding_cache.py:139-156`

`_maybe_evict()` acquires `self._lock` but is only called from `put()` which already holds it. Safe due to `RLock`, but unnecessary reentrancy.

---

## Positive Observations

1. **Lazy provider loading** — embedding model only loaded on first write
2. **Embedding identity validation** — catches provider swaps that would corrupt the index
3. **Cross-process model locking** — `fcntl.flock` with proper lifecycle management
4. **Content-addressed IDs** — `%{hash}` for inline text enables natural versioning
5. **ACT-R recency decay** — principled relevance scoring
6. **Embedding cache with binary serialization** — `struct.pack` for float32 is compact and fast
7. **System doc migration** — preserves user edits, tracks bundled hash
8. **Meta-doc resolution** — `surface:` system is clean contextual surfacing
9. **WAL mode + busy_timeout** on all SQLite connections
10. **Tag merge ordering** — existing -> config -> env -> user, with system tag protection
11. **Version archiving** with offset-based navigation

---

## Priority

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | LIKE injection | MEDIUM | Fixed |
| 2 | Frozen dataclass mutation | HIGH | Fixed |
| 3 | update/remember duplication | MEDIUM | Fixed |
| 4 | Embedding before change detection | MEDIUM | Fixed |
| 5 | Re-embedding old content | MEDIUM | Fixed |
| 6 | Error log in /tmp | LOW-MED | Fixed |
| 8 | touch/touch_many missing lock | LOW | Fixed |
| 7 | Tag scan performance | MEDIUM | Fixed |
| 9 | CLI global state | LOW-MED | Future |
| 10 | Migration encapsulation | MEDIUM | Future |
| 11 | Broad exception swallowing | LOW-MED | Future |
| 12 | Non-atomic dual-store writes | MEDIUM | Fixed |
| 13 | Unbounded version history | LOW | Won't fix (ok for now) |
| 14 | `_gather_context` double embedding | LOW | Fixed |
| 15-18 | Low-severity items | LOW | Future |
