# Summary/Content Unification Plan

Date: 2026-04-07
Updated: 2026-04-08
Status: Implemented

## Current status

This refactor has landed.

Current implementation state:

- `summary` is the single stored text field for notes and parts
- `PartInfo` has no `content`
- `document_parts` has no `content` column
- `parts_fts` indexes only `summary`
- `get_part()` returns part `summary` directly
- analyze output and part mutations are summary-only
- `set_content` no longer exists
- export writes format version `2` without `parts[].content`
- import still accepts old exports containing `parts[].content`

Follow-up fixes that are now part of the current branch state:

- OCR `set_summary` mutations explicitly request re-embedding
- delegated analyze normalization no longer emits a dead part `content` field
- migrated-part reindex handoff clears its in-memory list after enqueue
- migration coverage includes the Keeper-startup migration-to-pending-queue seam

The remaining sections below are preserved as implementation history and
rationale.

## Goal

Make `summary` the single canonical stored text field for both notes and parts.

For any stored note or stored part:

- there is one logical text
- that text lives in `summary`
- short source material is stored verbatim
- long source material is stored as extracted or summarized text

`content` remains only an ingest and workflow input. It is not a second
persisted semantic slot.

## Coordination

This plan overlaps slightly with
[remove-overview-part.md](/Users/hugh/play/keep/later/design/remove-overview-part.md).

Preferred order:

1. land overview-part removal first
2. then implement this plan against the simpler post-overview codebase

Reason:

- overview removal deletes `_upsert_overview_part()` and related write paths
- this plan otherwise spends effort touching code that should disappear anyway

If overview removal has not landed yet, this plan still works, but any
remaining `@P{0}`-specific `content` handling should be deleted rather than
refactored.

## Problem

The model currently mixes two different interpretations:

- notes mostly already treat `summary` as the canonical stored text
- parts still preserve a `summary` / `content` split that behaves like
  title/body

That creates drift across storage, search, retrieval, export, and background
processing.

The user-visible model should be simpler:

- the summary is the content
- one text per note
- one text per part

## Current State

Historical note: this section describes the pre-implementation state that the
refactor removed.

### Notes

Notes already mostly follow the target model.

In [keep/api.py](/Users/hugh/play/keep/keep/api.py#L2179), `_upsert()`
computes one `final_summary` and stores that in the canonical record. For short
inline content, `final_summary = content`, which already implements "the
content is the summary."

The public `Item` shape in
[keep/types.py](/Users/hugh/play/keep/keep/types.py#L533) exposes only:

- `id`
- `summary`
- `tags`
- `score`
- `changed`

So the note model is already close to the desired final state.

### Parts

Parts are where the semantic split still exists.

The split originally appeared in:

- `PartInfo.content` in
  [keep/document_store.py](/Users/hugh/play/keep/keep/document_store.py#L48)
- `document_parts.content` in
  [keep/document_store.py](/Users/hugh/play/keep/keep/document_store.py#L445)
- `parts_fts(summary, content)` in
  [keep/document_store.py](/Users/hugh/play/keep/keep/document_store.py#L529)
- part FTS search over `summary + content` in
  [keep/document_store.py](/Users/hugh/play/keep/keep/document_store.py#L2499)
- part retrieval in [keep/api.py](/Users/hugh/play/keep/keep/api.py#L4847),
  which currently prefers `part.content` when present
- analyze action normalization in
  [keep/actions/analyze.py](/Users/hugh/play/keep/keep/actions/analyze.py#L20)
- analyzer JSON parsing in
  [keep/analyzers.py](/Users/hugh/play/keep/keep/analyzers.py#L692)

### OCR / processing

Before implementation, the OCR mutation name still modeled a split, but the
runtime behavior was already closer to the final state than the name suggested.

- `ocr` emitted `set_content`
- `_apply_mutations()` handled `set_content`
- delegated task normalization also emitted `set_content`

Historical factual correction:

- the `set_content` consumer already discards the mutation's `content` field
- it only writes `documents.summary`, updates hashes, and embeds the summary

So this cleanup is mostly a rename and dead-argument removal, not a behavioral
change.

### Item-scoped actions

[keep/actions/_item_scope.py](/Users/hugh/play/keep/keep/actions/_item_scope.py)
formerly contained:

- `content = getattr(item, "content", None)`

But `Item` has no `content` attribute. That lookup is already dead code and
always falls through to payload content or `item.summary`.

So this cleanup is also simpler than it first appears: delete the dead lookup
and flatten the fallback logic.

### Export / import / docs

Before implementation, JSON export still wrote `parts[].content`. Import still
accepted and persisted that same shape.

The markdown export path already treats `parts.content` as vestigial in
[keep/cli_app.py](/Users/hugh/play/keep/keep/cli_app.py#L1988), which is a
strong signal that the intended model has already shifted.

Public docs still describe the old split in:

- [docs/KEEP-DATA.md](/Users/hugh/play/keep/docs/KEEP-DATA.md#L101)
- [docs/FLOW-ACTIONS.md](/Users/hugh/play/keep/docs/FLOW-ACTIONS.md#L291)
- [docs/KEEP-ANALYZE.md](/Users/hugh/play/keep/docs/KEEP-ANALYZE.md#L12)
- [docs/KEEP-EDIT.md](/Users/hugh/play/keep/docs/KEEP-EDIT.md#L23)
- [docs/PYTHON-API.md](/Users/hugh/play/keep/docs/PYTHON-API.md#L65)

## Design Principles

### 1. One stored text

For a persisted note or part, there is exactly one stored text field:

- `summary`

No second persisted field may compete for the same semantic.

### 2. `content` is workflow input, not stored state

`content` may still appear:

- as `put(content=...)`
- as fetched document text during ingest
- as OCR extracted raw text before normalization
- as analyzer prompt input
- as pending reindex payload text

But once persisted, the canonical text is `summary`.

### 3. Every shim must have a removal condition

Temporary compatibility code is acceptable only if it has:

- a named purpose
- a bounded scope
- an explicit final state where it disappears

### 4. Schema and writers change together

The summary-only parts schema and the summary-only writers are one atomic
change.

They must land in the same commit, or at least within the same daemon-restart
boundary. Otherwise a daemon running old code will try to write `content` into
a migrated schema that no longer has that column.

Implemented note:

- this landed as one schema-and-writer change, and the deferred-startup path
  was adjusted so migrated-part reindex enqueueing does not regress daemon
  startup

### 5. Stored text changes require embedding coordination

If migration changes canonical part text, the corresponding vector embedding
must be refreshed. A store is not fully migrated if SQLite says one thing and
Chroma still embeds the old prefix text.

## Final State

The end state of this work is:

- `PartInfo` has no `content`
- `document_parts` has no `content` column
- `parts_fts` indexes only `summary`
- `get_part()` returns `summary` directly
- item-scoped actions do not contain dead `item.content` fallback code
- analyze outputs parts as `{summary, tags, part_num}` only
- in-repo analyzers do not request or emit a second text field
- there is no `set_content` mutation
- JSON export writes no `parts[].content`
- export format version is bumped
- docs and tests no longer describe note or part semantics as `summary` vs
  `content`

Implemented outcome:

- complete, with old-export import compatibility retained at the import boundary

One compatibility path may remain longer:

- import support for old export files that still contain `parts[].content`

That compatibility is acceptable only at the import boundary, not as an
internal storage or runtime concern.

## Scope

### In scope

- part model cleanup
- SQLite schema migration for `document_parts`
- part FTS migration
- Chroma re-embedding coordination for migrated parts
- analyze contract cleanup
- OCR mutation cleanup
- export/import contract cleanup
- read/search path cleanup
- tests and docs for the new model

### Out of scope

- redesigning the note-level `put(content=...)` API surface
- rethinking multi-version overview parts
- redesigning note summarization prompts beyond what is required by this model
- unrelated runtime or daemon refactors

## Data Paths To Change

### Persistence

#### [keep/document_store.py](/Users/hugh/play/keep/keep/document_store.py)

Change:

- `PartInfo`
- `document_parts` schema
- `parts_fts` schema and triggers
- part read/write methods
- bulk import path
- migration ladder in `_migrate_schema()`

Required outcome:

- parts persist only `summary`
- FTS indexes only that one text

Historical implementation note:

- the schema version at planning time was `13`
- the migration ladder lives in
  [DocumentStore._migrate_schema()](/Users/hugh/play/keep/keep/document_store.py#L319)
- this change was implemented as `if current_version < 14: ...` with
  `SCHEMA_VERSION = 14`
- if another branch lands a schema bump first, use the next free version, but
  follow the same pattern

### Write path

#### [keep/api.py](/Users/hugh/play/keep/keep/api.py)

Keep:

- note-level `_upsert()` behavior where short content is stored directly as
  `summary`

Change:

- `get_part()`
- incremental analyze append path
- export/import shape
- any remaining overview-path `content` writes if overview removal has not
  landed yet

#### [keep/task_workflows.py](/Users/hugh/play/keep/keep/task_workflows.py)

Change:

- remove `set_content`
- replace it with a canonical-text mutation such as `set_summary`
- keep optional hash fields on that mutation
- normalize part writes to `summary` only

Historical note:

- this is not a behavior change in the consumer
- the old `set_content` consumer already stored only `summary + hashes`

#### [keep/_background_processing.py](/Users/hugh/play/keep/keep/_background_processing.py)

Change:

- delegated task result normalization for OCR and analyze
- any temporary summary/content compatibility output after rollout

### Actions

#### [keep/actions/analyze.py](/Users/hugh/play/keep/keep/actions/analyze.py)

Change:

- `_normalize_part()`
- emitted mutations

#### [keep/actions/ocr.py](/Users/hugh/play/keep/keep/actions/ocr.py)

Change:

- emit canonical text update, not a split-model mutation

#### [keep/actions/_item_scope.py](/Users/hugh/play/keep/keep/actions/_item_scope.py)

Change:

- remove dead `getattr(item, "content", None)` code
- use write payload content when available, else canonical `item.summary`

### Analyzer contract

#### [keep/analyzers.py](/Users/hugh/play/keep/keep/analyzers.py)

Change:

- `DECOMPOSITION_SYSTEM_PROMPT`
- `_parse_decomposition_json()`

The in-repo analyzers affected are:

- `SlidingWindowAnalyzer`
- `SinglePassAnalyzer` and its JSON decomposition path

Required outcome:

- in-repo analyzers produce summary-only parts
- parser can normalize legacy `{summary, content}` payloads temporarily

Important note:

- `_extract_line_ranges()` matches part summaries to section headings
- it does not depend on `part["content"]`
- removing `content` from analyzer output therefore does not break line-range
  tagging for URI-sourced notes

### Interchange and user-visible contract

#### [keep/api.py](/Users/hugh/play/keep/keep/api.py)

Change:

- JSON export/import
- export format version

#### Docs

Update:

- [docs/KEEP-DATA.md](/Users/hugh/play/keep/docs/KEEP-DATA.md)
- [docs/FLOW-ACTIONS.md](/Users/hugh/play/keep/docs/FLOW-ACTIONS.md)
- [docs/KEEP-ANALYZE.md](/Users/hugh/play/keep/docs/KEEP-ANALYZE.md)
- [docs/KEEP-EDIT.md](/Users/hugh/play/keep/docs/KEEP-EDIT.md)
- [docs/PYTHON-API.md](/Users/hugh/play/keep/docs/PYTHON-API.md)

Remove split-model language and describe one-text semantics clearly.

## Migration Plan

### SQLite schema migration

Bump `SCHEMA_VERSION` and rebuild `document_parts` rather than trying to alter
it in place.

Why rebuild:

- dropping columns is fragile across SQLite versions
- rebuild lets us rewrite rows and FTS triggers atomically
- rebuild is consistent with the existing migration ladder style

### Data migration rule

For each legacy part row:

- if `content` is non-empty, migrate canonical text from `content`
- else migrate canonical text from existing `summary`

That rule matches the target semantics:

- there is one stored part text
- legacy `content` is the fuller text when present

### Known divergent rows

In Hugh's store, the known divergent rows are OCR-derived PDF page parts from a
single document. Those rows currently look like:

- `summary = content[:~200]`
- `content = full OCR page text`

Migration intentionally upgrades those parts to full text. This is a positive
change, but it is user-visible:

- FTS matches will improve
- semantic ranking will change after re-embedding
- exported summaries for those parts will become much longer

### SQLite migration mechanics

1. Create a new parts table without `content`.
2. Copy old rows into the new table, applying the normalization rule above.
3. Drop old `parts_fts`.
4. Drop old triggers referencing `content`.
5. Swap in the new table.
6. Recreate `parts_fts(summary)` and rebuild it.

### Chroma re-embedding strategy

The migration must also handle vector-store consistency.

Problem:

- for rows where canonical text changes from legacy `summary` to legacy
  `content`, the existing part embedding was built from the old summary
- after migration, SQLite would hold the new canonical text but Chroma would
  still search over an embedding of the old prefix text

Decision:

- queue reindex tasks for migrated parts whose canonical text changed
- do not accept stale embeddings as the steady state
- do not inline re-embed under the SQLite migration lock

Mechanics:

1. During migration, identify rows where migrated `summary` differs from the
   legacy stored `summary`.
2. After schema migration completes, enqueue targeted `reindex` tasks for those
   part IDs using the same task shape used by
   [Keeper.enqueue_reindex()](/Users/hugh/play/keep/keep/api.py#L1093):
   - `id = "<base_id>@p<part_num>"`
   - payload summary = migrated canonical summary
   - metadata includes `part_num`, `base_id`, and part tags
3. Let the existing daemon reindex path write fresh embeddings.

Only changed rows need reindex. Rows whose canonical text is unchanged should
not be re-embedded.

### Export compatibility

Import should continue accepting old export files containing:

- `parts[].content`

But import must normalize them immediately into the single-text model.

Export should move from format version 1 to version 2 when `parts[].content`
disappears from emitted JSON.

## Implementation Stages

This should land in stages on one branch, but the intended merge state is the
full cleanup, not a halfway house.

### Stage 1: Add bounded compatibility readers

Purpose:

- allow the repository to pass through the transition without breaking old
  analyzer payloads or old export files

Allowed shims:

- analyzer output normalization accepts legacy `{summary, content}` and
  collapses it to one canonical text
- import normalization accepts `parts[].content` from old backups

Not allowed:

- adding any new public or internal writer that preserves the split

Removal condition:

- analyzer-side normalization is removed once all in-repo analyzers, task
  outputs, tests, and docs are summary-only

Longer-lived compatibility:

- old export import support may remain if historical backup restore remains a
  supported contract

### Stage 2: Atomic schema flip and writer cleanup

Tasks:

- migrate `document_parts` to summary-only
- remove `PartInfo.content`
- rebuild `parts_fts`
- replace `set_content` with the canonical-text mutation
- make analyze, OCR, task mutations, and import write canonical `summary` only
- queue reindex tasks for changed migrated parts

This stage is one atomic change.

Requirements:

- schema migration and writer cleanup land together
- in-flight daemons must be restarted
- old daemons must not continue running against the new schema

Implementation note:

- use the existing daemon version guard pattern
- if needed, make old daemons refuse startup when the on-disk schema is newer

### Stage 3: Flip read, search, and export surfaces

Tasks:

- `get_part()` returns `summary`
- `resolve_item_content()` deletes dead `item.content` fallback code
- part FTS indexes only `summary`
- JSON export emits summary-only parts
- docs, examples, and tests stop describing the split

### Stage 4: Remove temporary rollout shims

Delete:

- analyzer normalization that existed only for transition
- any temporary mutation aliases introduced during rollout

Keep only:

- old-export import compatibility, if desired

This stage is part of this plan, not follow-up cleanup.

## Shim Inventory

### Shim A: Analyzer output normalization

Scope:

- normalize legacy analyzer output shaped like `{summary, content, tags}`

Purpose:

- avoid breaking old analyzer implementations while in-repo writers are
  converted

Required behavior:

- collapse legacy output to one canonical text before persistence

Removal condition:

- all in-repo analyzers, delegated outputs, tests, and docs are summary-only

Final state:

- parser and action normalization expect and emit summary-only parts

### Shim B: Old export import compatibility

Scope:

- `import_data()` and `DocumentStore.import_batch()`

Purpose:

- continue to restore historical exported data

Required behavior:

- if `parts[].content` exists, normalize to canonical `summary`
- never reintroduce split semantics into the store

Removal condition:

- only if the project intentionally drops backward compatibility for older
  export versions

Final state if retained:

- compatibility exists only at the import boundary

## Tests Touched

At minimum, the implementation should expect to update:

- `tests/test_parts.py`
- `tests/test_incremental_analyze.py`
- `tests/test_data_export.py`
- `tests/test_part_line_ranges.py`
- `tests/conftest.py`

If overview removal has not landed first, the temporary overlap with
`tests/test_analyze_overview.py` should disappear as part of that earlier plan,
not be carried into this one.

## Testing Plan

### Migration tests

Add or update tests to verify:

- legacy DB with `document_parts.content` migrates cleanly
- when legacy `content` is populated and differs from `summary`, migrated
  `summary` uses legacy `content`
- when legacy `content` is empty, existing `summary` is preserved unchanged
- when both are empty, migration does not crash
- the migration is idempotent when re-run on an already-migrated store
- `parts_fts` works after migration
- FTS query results are unchanged for rows whose canonical text did not change
- triggers rebuild correctly

### Reindex / embedding tests

Add or update tests to verify:

- changed migrated parts are enqueued for `reindex`
- unchanged parts are not spuriously re-enqueued
- the reindex payload uses migrated canonical summary text

### Model and behavior tests

Update tests to verify:

- `PartInfo` no longer exposes `content`
- `get_part()` returns canonical `summary`
- analyze stores one text per part
- OCR updates canonical head text and hashes without `set_content`
- item-scoped actions operate correctly using payload content or canonical
  `summary`

### Export/import tests

Update tests to verify:

- export omits `parts[].content`
- export format version becomes 2
- import accepts old shape with `parts[].content`
- imported parts are stored summary-only
- import still queues reindex appropriately

### Analyzer contract tests

Update tests to verify:

- `SlidingWindowAnalyzer` path is summary-only
- `SinglePassAnalyzer` JSON parser is summary-only
- `_extract_line_ranges()` behavior is unchanged

### Shim-removal tests

Add at least one test that proves the final state after shim removal:

- no in-repo analyzer emits `content`
- no mutation dispatcher path recognizes `set_content`
- normalization output after stage 4 contains no `content` field

## Risks

### Prompt-behavior risk

Even though `content` is not needed for persistence, asking the model for both
`summary` and `content` may have served as a decomposition aid.

Before deleting the in-repo prompt's `content` requirement, run an A/B
comparison on representative notes:

- ordinary long inline notes
- URI-sourced markdown with headings
- OCR-derived PDF content

This is not a blocker, but it should be checked explicitly.

### Legacy divergent part rows

Some stores contain parts where legacy `summary` and `content` differ
materially.

Decision:

- migrate canonical text from `content` when present
- queue reindex for rows whose canonical text changed

This is intentional and should be called out in the migration notes.

### Silent partial cleanup

The main implementation risk is removing the DB column but leaving old
contracts around in:

- docs
- tests
- delegated output normalization
- flow mutation handling
- export versioning

This plan explicitly treats those as first-class work.

## Acceptance Criteria

- Notes and parts each have one canonical stored text: `summary`.
- The SQLite parts table has no `content` column.
- Part FTS indexes only canonical part text.
- Changed migrated parts are queued for reindex and receive fresh embeddings.
- `get_part()` returns the canonical part text without fallback.
- OCR and analyze do not emit split-model mutations.
- Export no longer emits `parts[].content`.
- Export format version is bumped.
- Import still restores older exports by normalizing them into summary-only
  storage.
- Temporary in-repo compatibility shims used during rollout are removed by the
  end of the branch.
- Docs and tests consistently describe the summary-only model.

## Conditions Of Satisfaction

The work is done when there is no longer any ambiguity in the model:

- for notes, `summary` is the content
- for parts, `summary` is the content
- `content` is only an ingest or workflow input and never a competing persisted
  semantic
