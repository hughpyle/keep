# Remove the `@P{0}` overview part

Design / refactoring plan — 2026-04-07, updated 2026-04-08

Status: Implemented

## Current status

This refactor has landed.

Current implementation state:

- analyzed parts are numbered `1..N`
- no analysis path writes a synthetic `@P{0}` part
- `get_part()` and projection rendering do not special-case overview parts
- the `_part_type: overview` marker is no longer part of the runtime model
- legacy `part_num = 0` rows are cleaned up at Keeper startup

Post-implementation note:

- the startup cleanup is now persisted as a one-shot store flag
  (`legacy_overview_parts_cleaned`) so the legacy overview scan does not stay
  on the hot path forever

The remaining sections below are kept as implementation history and rationale.

## Decision

Remove the `@P{0}` overview-part feature entirely.

This includes:

- the write paths that synthesize and store the overview part
- the read paths that special-case `part_num == 0`
- the search/render logic that treats `@P{0}` as a story line
- the `_part_type: overview` marker
- any legacy `part_num = 0` rows and matching vector entries

The final state is:

- analyzed parts are numbered `1..N`
- `part_count` and `_total_parts` are the same number
- no code writes or reads a synthetic overview part
- no tests or docs describe `@P{0}` as part of the model

## Why remove it

### Low observed usage

Hugh's main store snapshot showed:

```sql
SELECT MIN(part_num), MAX(part_num) FROM document_parts;  -- 1, 159
SELECT COUNT(*) FROM document_parts WHERE part_num = 0;   -- 0
```

So the feature is not present in the main analyzed corpus.

Two reasons explain that:

1. The write gate is narrow. The full-analysis path only creates the overview
   when `len(chunk_dicts) >= 2 and raw_parts`, which excludes the dominant
   first-analysis, single-version case.
2. The feature was introduced late, on 2026-03-12, so older analyzed notes
   never gained one unless they were force-reanalyzed.

That is strong evidence for low usage, but it is not evidence that the code is
dead. If a store does contain `@P{0}`, current read paths still surface it.
This plan therefore includes an explicit data cleanup step.

### The feature is internally inconsistent

The two write paths do not synthesize the same thing:

- full analysis summarizes concatenated raw version content
- incremental append summarizes already-summarized part text

So even if we wanted an overview concept, the current `@P{0}` implementation is
not a stable model to keep.

## Scope

This refactor removes the overview-part feature only.

Out of scope:

- the separate `summary` vs `content` cleanup for parts
- redesigning multi-version synthesis as a parent-note field or projection
- changing ordinary part rendering beyond removing `@P{0}` behavior

## Final state

The implementation is complete only when all of the following are true:

- `analyze()` never creates `@P{0}`
- incremental analyze never regenerates an overview part
- `get_part()` does not mention or compensate for overview parts
- projection/render logic never prepends or falls back to `part_num == 0`
- `_part_type` is not written or read anywhere
- stores with legacy `part_num = 0` rows are cleaned up in both SQLite and
  Chroma
- tests and docs no longer model `_part_num = 0` as a special overview

Implemented outcome:

- complete

## Current implementation map

### Writers

Historical note: this section describes the pre-removal implementation surface.

- `keep/api.py`
  - `analyze_item()` phase-4 overview generation
  - `_generate_vstring_overview()`
  - `_append_incremental_parts()` tail regeneration
  - `_upsert_overview_part()`

### Readers / projections

- `keep/api.py`
  - `get_part()` docstring and `_total_parts` adjustment
- `keep/projections.py`
  - `Story:` rendering block that prepends or falls back to `@P{0}`
  - detail rendering that currently includes `@P{0}` in `Key topics:`

### Data / storage

- `keep/document_store.py`
  - `document_parts` rows with `part_num = 0`
  - FTS mirror rows via existing delete triggers
  - `upsert_single_part()` docstring mentions overview-only usage
- `keep/store.py`
  - vector entries stored as `<id>@p0`

### Tests / docs

- `tests/test_analyze_overview.py`
- `tests/test_incremental_analyze.py`
- `tests/test_render_find_context.py`
- `tests/test_find_context_projection_plan.py`
- `tests/test_deep_edges.py`
- `docs/KEEP-ANALYZE.md`

## Implementation plan

Implement this as one branch, in stages, with a clean final state and no
temporary semantic shims left behind.

### Stage 1: Add the data cleanup at the correct layer

This is a data migration, not a SQLite schema migration.

Do not put Chroma cleanup inside `DocumentStore`. `DocumentStore` is SQLite-only
and has no vector-store access. The cleanup must be owned by the Keeper/runtime
layer where both stores are available.

Implementation shape:

1. Add a Keeper-owned, idempotent cleanup helper for overview parts.
2. Query SQLite for legacy overview rows:
   `SELECT collection, id FROM document_parts WHERE part_num = 0`.
3. For each row, delete the matching Chroma entry using the existing targeted
   vector delete primitive:
   `self._store.delete_entries(chroma_coll, [f"{id}@p0"])`.
4. Delete the SQLite rows with:
   `DELETE FROM document_parts WHERE part_num = 0`.
5. Rely on existing `parts_fts` delete triggers to clean FTS rows.
6. Make the helper safe to run repeatedly; no-op when no rows exist.

This helper may run during Keeper startup migration/maintenance, or via a
Keeper-owned explicit repair path, but it must live above `DocumentStore`.
Implemented as a Keeper-owned startup cleanup.

Current implementation note:

- the cleanup is idempotent and persisted with a store-level completion flag

### Stage 2: Remove the write paths

Delete these from `keep/api.py`:

- the phase-4 overview block in `analyze_item()`
- `_generate_vstring_overview()`
- the overview-regeneration tail in `_append_incremental_parts()`
- `_upsert_overview_part()`

Also remove the `_part_type: overview` writer, since no remaining code should
emit that tag.

### Stage 3: Remove the read-side special cases

In `keep/api.py:get_part()`:

- remove overview wording from the docstring
- remove the `has_overview` lookup
- set `_total_parts` directly from `part_count`

In `keep/projections.py`:

- remove the `part_num == 0` prepend behavior when focused parts exist
- remove the `no focus -> show only @P{0}` fallback
- keep only the focused-part neighborhood selection for `Story:`
- ensure detail rendering treats all parts uniformly and does not rely on
  overview-first ordering

Expected user-visible change:

- when a parent note has parts but no focused part, there is no synthetic
  `Story:` line anymore
- the parent note summary remains the top-level summary
- `Key topics:` shows ordinary parts only

### Stage 4: Update tests and docs in the same change

Delete:

- `tests/test_analyze_overview.py`

Update:

- `tests/test_incremental_analyze.py`
  - remove `part_num > 0` filters that existed only to skip overview parts
- `tests/test_render_find_context.py`
  - replace `part_num=0` fixtures with ordinary parts where needed
  - update expectations that currently include overview text in `Key topics:`
- `tests/test_find_context_projection_plan.py`
  - remove expectations that depend on `story` existing because of `@P{0}`
- `tests/test_deep_edges.py`
  - replace the `part_num=0` fixture row with a normal part; the test is about
    scoped FTS behavior, not overview semantics
- `docs/KEEP-ANALYZE.md`
  - change `_part_num` documentation from `0 for the optional vstring overview`
    to plain `1..N` part numbering

Review adjacent docs for stale mention of `@P{0}` and remove any remaining
references in the same change.

### Stage 5: Verify the final state

Verification is complete only when all of these pass:

1. Unit and integration tests for analyze, parts, projection, and rendering.
2. Full suite.
3. Store migration check on:
   - a clean store with no overview rows
   - a synthetic store containing at least one `part_num = 0` row and matching
     `<id>@p0` vector entry

The migration test must prove:

- SQLite `document_parts` loses the overview row
- FTS no longer returns it
- Chroma no longer contains `<id>@p0`
- repeated runs are safe and leave the store unchanged

Implemented outcome:

- tests cover both legacy row cleanup and the persisted one-shot behavior

## Concrete file plan

### `keep/api.py`

Remove:

- phase-4 overview generation in `analyze_item()`
- `_generate_vstring_overview()`
- overview regeneration in `_append_incremental_parts()`
- `_upsert_overview_part()`
- overview wording and count adjustment in `get_part()`

Keep:

- `is_vstring`; it is still part of incremental-analysis gating and is not an
  overview-only concept

### `keep/projections.py`

Refactor:

- `Story:` selection to operate only on focused part neighborhoods
- detail rendering to stop surfacing `@P{0}` as a pseudo-part

### `keep/document_store.py`

Change:

- reword the `upsert_single_part()` docstring so it no longer claims
  overview-specific use

Do not:

- add Chroma-aware migration logic here
- bump SQLite schema version just for overview cleanup

### `keep/store.py`

No new storage model is required.

Use the existing `delete_entries()` helper for targeted `<id>@p0` cleanup.

### Tests

Delete or update the files listed in Stage 4.

### Docs

Update `docs/KEEP-ANALYZE.md` and any neighboring docs that still describe
overview parts as part of the note model.

## Risk

Low to moderate.

The code is localized, and the main known store has zero rows, but this is not
pure dead-code deletion because stores with legacy overview rows would still see
the behavior today. The main risks are:

- forgetting the Chroma cleanup and leaving stale searchable `<id>@p0` entries
- removing the code but missing stale test/doc assumptions
- changing projection behavior without updating rendering expectations

The staged plan above addresses those directly.

## Non-goals and follow-up

Do not bundle these into the same change:

- removing `parts.content`
- redesigning a future multi-version synthesis feature
- broader projection/render cleanup unrelated to `@P{0}`

If a future overview concept is wanted, it should be designed as either:

- a parent-note field, or
- a projection-time synthesis

not as a magic-numbered fake part.
