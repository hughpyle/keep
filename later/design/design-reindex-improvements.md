# Reindex improvements

Review date: 2026-04-08. Investigation conducted against HEAD of main (v0.129.3).

## Scope

`reindex` in this codebase means: re-embed summaries into the vector store after an
embedding model / dimension change. It does **not** re-read source files, re-analyze,
re-summarize, re-extract links, or re-tag. The narrowness is correct; the problems are
about how inefficiently we do that narrow work and about several silent correctness bugs.

Entry points: `keep pending --reindex` CLI, auto-trigger on embedding-identity change
(`api.py:1032-1091`), edge-backfill vivify (`_background_processing.py:1069-1097`),
`import_data` (`api.py:1332-1354`), startup reconcile (`api.py:1821-1827`). The
model-change / CLI path uses `Keeper.enqueue_reindex` (`api.py:1093`); `import_data`
and startup reconcile enqueue reindex tasks directly. All roads converge on pending
`reindex` entries and the consumer `_process_pending_reindex`
(`_background_processing.py:925`).

Note: the L2→cosine and task-prefix one-time migrations have already run everywhere;
those code paths remain in the tree but are dead in practice and don't need optimizing.

---

## Correctness issues

**A1. Embedding provider torn down between every item.**
`_background_processing.py:440, 448`. `_release_embedding_provider()` runs after every
single reindex item. For local MLX providers this unloads and reloads GPU weights on
every item. The code comment says this lets interactive processes grab the model lock
between items — correct for slow summarize/ocr tasks, wrong for millisecond-class
reindex items. Also closes the embedding SQLite cache mid-batch.

**A2. No dedup on the reindex path.**
`_background_processing.py:944` calls `provider.embed(item.content)` one item at a time
with no dedup. Compare `_process_pending_embed` at L910 which calls
`_try_dedup_embedding`. Two notes with identical summaries each get their own embed call
on reindex.

**A3. `enqueue_reindex` is not atomic with respect to concurrent writes.**
`api.py:1093-1144`. It calls `list_ids` once, then `get()`s each ID. A concurrent
writer can delete or version-archive a doc mid-iteration. The main-doc consumer branch
checks for deletion (L968-970) but the version and part branches do not. A purged
version causes `upsert_version` to write a ghost Chroma entry pointing to a
no-longer-existing doc-store row.

**A4. Version/part branches don't guard against missing rows.**
`_background_processing.py:946-965`. Unlike the main-doc branch which calls
`self._document_store.get(...)` and returns on None, the version/part branches trust the
metadata captured at enqueue time. A doc version purged between enqueue and consume
creates an orphaned Chroma entry that reconcile then has to clean up. On same-dimension
model changes, where the collection is not dropped up front, simply skipping the missing
row would still leave the old `@v` / `@p` embedding behind.

**A5. Version/part tags are stale by the time the item is consumed.**
`api.py:1111-1131`. For the main-doc branch, reindex re-fetches authoritative tags from
the doc store. For version/part branches, tags captured at enqueue time are written to
Chroma, even if they've since changed.

**A6. Cancellation is not threaded into the embed call.**
`_background_processing.py:373-375`. `shutdown_check` is checked once per item before
running it, not inside the embed call. A slow local-model embed cannot be interrupted
mid-item, adding up to several seconds of latency to shutdown during a reindex drain.

**A7. `list_versions(limit=100)` silently truncates.**
`api.py:1116`. Docs with more than 100 archived versions have older ones missed by
reindex. They stay in Chroma with old-dimension embeddings; on query, Chroma raises a
dimension-mismatch error for those rows. The export path at `api.py:1219` uses
`limit=10000`; reindex should do the same or stream.

**A8. System docs are enqueued then deleted.**
`_background_processing.py:932-934`. If a system doc (ID starting with `.`) winds up on
the reindex queue, its Chroma row is deleted. But `enqueue_reindex` at `api.py:1107`
doesn't filter system IDs (compare `export_iter` at L1178 which does), so on every
model change, all system docs are silently removed from the vector store.

**A9. Main-doc branch writes enqueue-time summary, not authoritative summary.**
`_background_processing.py:975`. The consumer uses `item.content` (captured at enqueue
time) even though it re-fetches `doc` from the doc store. If a `summarize` task ran
between enqueue and consume, the fresh summary is discarded and the old one is embedded.
Should use `doc.summary`.

**A10. Auto-vivified stubs enqueue with `content=target_id`.**
`_background_processing.py:1088`, `api.py:1821`. Both auto-vivify paths (edge-backfill
and startup reconcile) pass the URI string as the embed content. The consumer embeds it,
producing a noise vector for an empty stub. The L935 guard
(`if not item.content.strip()`) does NOT save it because the URI string is non-empty.

**A11. `save_config` failure is invisible.**
`api.py:473-477, 455-459`. If `save_config` fails silently (`.debug` log level), any
migration flag that depends on it (e.g. embed identity) never persists. Logged at a
level that no operator will see.

---

## Performance issues

**B1. Daemon runs `process_pending(limit=1)`.**
`console_support.py:1427`. Every embedding-provider setup, every Chroma write, every
`poll_watches` invocation, every `time.sleep(0.1)` fires once per item. All downstream
batching is defeated before it starts.

**B2. `time.sleep(0.1)` fires once per item instead of once per batch.**
`_background_processing.py:465`. The sleep exists to give local users interrupt time and
to stop the queue from monopolising the CPU — correct intent. But at limit=1 it fires
between every single reindex item, adding 1 000 s (~17 min) of dead time on a 10k
reindex. The right granularity is once per batch (i.e. once per `process_pending` call),
not once per item.

**B3. Per-item `_release_embedding_provider()`.** (See A1.) For local MLX: GPU model
unload/reload per item — the dominant cost on large reindexes. For cloud providers:
embedding cache open/close per item.

**B4. `embed_batch` is never used on the reindex path.**
`_background_processing.py:944`. Each item calls `provider.embed(text)`. Every provider
exposes `embed_batch`. For Voyage/OpenAI/Gemini, batching 64-128 texts per call gives
10-50× throughput and fewer rate-limit penalties. For local MLX, `embed_batch` runs a
single fused forward pass — 3-8× faster than one call per item.

**B5. Single-row Chroma writes; `upsert_batch` is unused on the reindex path.**
`store.py:942-968`. Each write acquires the fcntl cross-process lock, increments the
epoch (touching `.chroma.epoch`), writes one row to HNSW, then releases. Every other
`keep` process in the same store reloads its Chroma client on its next write because of
the epoch bump. For 10k items: 10k lock acquires, 10k epoch bumps, 10k single-row HNSW
mutations. No `upsert_version_batch` or `upsert_part_batch` exists.

**B6. `enqueue_reindex` is N+1 against the doc store.**
`api.py:1100-1131`. For each doc: one `get(doc_id)` SQL query, one `list_versions()`
query, one `list_parts()` query. For 10k docs: ~30k SQL round-trips. `get_many` already
exists at `document_store.py:2105`; there is no streaming `list_all_versions` or
`list_all_parts` helper. Enqueue happens in the CLI process synchronously before the
daemon starts draining, so this latency is user-visible.

**B7. `PRAGMA synchronous=FULL` on pending_summaries.db.**
`pending_summaries.py:90-92`. WAL mode is set but `synchronous` is left at default
(`FULL`), forcing fsync per commit. For a 30k-item enqueue at `BATCH_SIZE=200` this is
150 full fsyncs. `synchronous=NORMAL` is safe with WAL and is 2-3× faster for bulk
writes.

**B8. `poll_watches` + prune + replenish fire between every reindex item.**
`console_support.py:1435-1463`. Because `process_pending(limit=1)` makes each daemon
tick consume exactly one item, watch polling stats every watched file between every
reindex write. During a 10k reindex this is 10k watch polls — increased filesystem
pressure and extra windows for watch-triggered work to race with reindex writes
(amplifying the `_bump_epoch` / `_check_freshness` reload cost at `store.py:158-197`).

**B9. `_check_freshness()` may rebuild the Chroma client mid-batch.**
`store.py:158-197`. Called on every `_write_guard` acquire. If another process bumps the
epoch, the next reindex write triggers a full `PersistentClient` rebuild. Since reindex
itself bumps the epoch after every write, two concurrent processes would thrash each
other's HNSW loads.

**B10. `upsert` does a `coll.get()` read just to preserve `_created`.**
`store.py:313-321`. One extra Chroma read per main-doc reindex write to avoid clobbering
the `_created` metadata field — data the document store already has and that the
enqueue step already loaded.

---

## Unnecessary work

**U1. Duplicate summaries re-embedded.** See A2. A typical store has many identical
summaries (Git ingest, system docs, stubs). Reindex embeds each occurrence; the embed
path already has dedup.

**U2. `upsert` reads `_created` from Chroma unnecessarily.** See B10. Accept an
optional `created_at` parameter so callers that already have it skip the read.

**U3. Provider released and rebuilt between every item.** See A1/B3.

**U4. Sleep fires once per item rather than once per batch.** See B2. The sleep itself
is necessary; its granularity is the problem.

**U5. System docs enqueued and then deleted.** See A8. Filter at enqueue time.

**U6. Auto-vivified stub enqueue produces noise vectors.** See A10. Either skip enqueue
(next real write will trigger embed), or pass empty content so the L935 consumer guard
applies. Fix both auto-vivify producers; currently both pass the URI string so the guard
doesn't fire.

**U7. `poll_watches` called 10k times during a full reindex drain.** See B8.

---

## Plan

### Quick wins (hours, low risk)

1. **Stop releasing embedding provider between reindex items.** Gate
   `_release_embedding_provider()` at `_background_processing.py:440,448` on task type:
   only release after the whole `process_pending` call returns empty, not after each
   item. Biggest single speedup on local providers. Fixes A1/B3/U3.

2. **Move `time.sleep(0.1)` to batch granularity.** The sleep is intentional (gives
   local users interrupt time, prevents queue monopoly) but should fire once per
   `process_pending` call, not once per item. Move it out of the per-item loop in
   `_background_processing.py:465` to the call site in the daemon tick. With a larger
   batch limit (item 3), a single sleep after draining 64-128 items is sufficient
   backpressure. Fixes B2/U4.

3. **Bump `process_pending` limit for reindex.** Raise `limit` in
   `console_support.py:1427` to 64-128, or detect a reindex-heavy queue and bump
   dynamically. Fixes B1.

4. **Filter system-doc IDs in `enqueue_reindex`.** Add `if doc_id.startswith('.'): continue`
   at `api.py:1107`, matching `export_iter` at L1178. Fixes A8/U5.

5. **Remove `limit=100` cap in `list_versions` inside `enqueue_reindex`.**
   `api.py:1116`. Use `limit=None` or `limit=10000`. Fixes A7.

6. **Skip enqueue for auto-vivified empty-summary stubs.** In both
   `_background_processing.py:1087-1097` and `api.py:1821-1827`, either skip the reindex
   enqueue entirely or pass `content=""` instead of `content=target_id` so the L935
   consumer guard short-circuits the embed. Fixes A10/U6.

7. **Add dedup to `_process_pending_reindex`.** Mirror `_try_dedup_embedding` from the
   embed path into the reindex consumer. Fixes A2/U1.

8. **Guard version/part branches against missing rows and remove stale entries.** In
   `_background_processing.py:946-965`, re-fetch from the doc store at consume time
   rather than trusting enqueue-time metadata. If the version/part row is gone, delete
   the corresponding `@v` / `@p` Chroma ID instead of just skipping it; otherwise
   same-dimension model changes leave stale old-model embeddings behind. Fixes A3/A4/A5.

9. **Use `doc.summary` not `item.content` on the main-doc branch.**
   `_background_processing.py:975`. Fixes A9.

10. **Promote `save_config` failure to WARNING.** `api.py:473,459`. Fixes A11.

11. **Set `PRAGMA synchronous=NORMAL` on `pending_summaries.db`.**
    `pending_summaries.py:90`. Safe with WAL. Fixes B7.

12. **Add throughput/ETA log line every N items in `process_pending`.** Makes reindex
    survivable on large stores.

### Medium effort (days)

13. **Bulk fetch in `enqueue_reindex`.** Replace per-ID `get()` with `get_many` (exists
    at `document_store.py:2105`), add `list_all_versions(collection)` and
    `list_all_parts(collection)` streaming helpers (single SELECT each). Fixes B6.

14. **Batch embed calls in the reindex consumer.** Collect all texts for a dequeued
    batch, call `provider.embed_batch(texts, task=EmbedTask.DOCUMENT)` once, fan out to
    upsert. The embedding cache at `embedding_cache.py:341` already handles batch misses
    correctly. Fixes B4.

15. **Batch Chroma writes.** Add `upsert_version_batch` and `upsert_part_batch` to
    `store.py` (mirroring `upsert_batch` at L942). Collect all reindex upserts for a
    dequeued batch and issue at most three `upsert_batch` calls (main/versions/parts)
    under a single `_write_guard` acquire and single `_bump_epoch`. Fixes B5/B9.

16. **Accept optional `created_at` in `Chroma.upsert` to skip the read.**
    `store.py:313-321`. Reindex always has `created_at` from the document store. Fixes U2/B10.

17. **Gate `poll_watches` + prune + replenish to fire at most every T seconds during
    reindex drain.** `console_support.py:1435-1463`. Fixes B8/U7.

18. **Atomic snapshot for `enqueue_reindex`.** Wrap `list_ids`/`get_many`/
    `list_versions`/`list_parts` in a single read transaction. Fixes A3.

19. **Use task-aware reindex enqueue semantics instead of blind replace/ignore.**
    `pending_summaries.py:315-319`. A rerun should refresh content/metadata for queued
    reindex items without resetting healthy in-flight work, and it must be able to
    resurrect failed reindex rows. `INSERT OR IGNORE` is wrong because it leaves failed
    rows and stale payloads untouched; `INSERT OR REPLACE` is wrong because it resets
    attempts/status unconditionally.

### Larger refactors (weeks)

20. **Split `reindex` into `reindex-embed` and `reindex-rewrite` task kinds.** Use the
    cheap `reindex-rewrite` path whenever the stored embedding is still valid (tag-only
    edits, doc renames). `reindex-embed` is reserved for cases where the embedding text
    itself must be recomputed. This is the architectural fix that prevents future
    migrations from silently over-firing.

21. **Cloud concurrency.** For API-backed providers, issue N parallel `embed_batch`
    calls while the consumer streams texts. 4-8× throughput improvement. Gate by
    provider feature flag. Requires careful rate-limit and `_write_guard` coordination.

22. **Dedicated reindex drain loop in the daemon.** Detect a non-empty reindex backlog
    and enter a tight drain loop (respecting cancellation and a max-wallclock bound)
    rather than interleaving with the normal tick. Subsumes items 3 and 17.

23. **End-to-end progress reporting.** Record a `reindex_run` row (started_at, total,
    processed, failed) in the store SQLite. Expose via the daemon HTTP query server so
    the CLI can show a live progress bar. Write a "last successful reindex" timestamp and
    support `--resume`.

---

## Recommended order of attack

Phase 1: correctness fixes that remove silent wrongness with minimal code churn:
items 8, 9, 6, 5.

Phase 2: cheap throughput fixes with the biggest immediate payoff:
items 1, 2, 3.

Phase 3: reduce repeated work and recover the remaining bulk performance:
items 7, 13, 14, 15.

Everything else is confirmed OK to defer for now. In particular, items 4, 10, 11, 12,
16, 17, 18, and 19 are useful follow-on improvements but are not on the critical path
once phases 1-3 land; items 20-23 remain explicitly deferred architectural work rather
than immediate implementation targets.
