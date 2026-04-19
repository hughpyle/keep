# Design: Tags, Conditions, and Item Context

Status: **phases 1–5 implemented** (type/kind split, unified item context, conditional edges + inverse find, conversation tagging, _when on prompts + classifier)

## Motivation

The `type` tag currently serves two roles:

1. **Entity-type** — what the thing fundamentally *is* in a graph model:
   `conversation`, `paper`, `vulnerability`, `person`, `project`, ...

2. **Content-kind** — a reflective annotation about what you got out of it:
   `learning`, `breakdown`, `gotcha`, `reference`, `teaching`, `meeting`,
   `pattern`, `possibility`, `decision`

These are different axes. A conversation can contain a decision. A paper can
surface a learning. The entity-type is the graph node label; the content-kind
is a property of the note.

Overloading `type` blocks its use as the primary entity-type tag for a
comprehensive graph model, and creates ambiguity when both an entity-type and
content-kind apply to the same note.

## Design

### New tag: `kind`

Move all 9 current content-kind values from `type` to `kind`:

| `kind` value | Meaning |
|---|---|
| `learning` | Hard-won insight worth remembering |
| `breakdown` | A failure where assumptions were revealed |
| `gotcha` | A known trap or non-obvious pitfall |
| `reference` | An indexed document, file, or URL |
| `teaching` | Source material for study or practice |
| `meeting` | Notes from a meeting or conversation |
| `pattern` | A recognized recurring structure |
| `possibility` | An exploration of options, no commitment yet |
| `decision` | A significant choice with recorded reasoning |

### `type` becomes entity-type only

`type` is reserved for the fundamental entity classification in the graph:
`conversation`, `paper`, `vulnerability`, `file`, `person`, `project`, etc.
Prompt-matching rules already use entity-type values (`type=conversation`,
`type=paper`) — those stay unchanged.

### `kind` already exists in limited use

The hermes provider already writes `kind=compression-snapshot` and
`kind=delegation` as tags on operational notes. These are valid content-kind
values and coexist naturally with the new vocabulary. No conflict.

### Relationship between tags after the split

| Tag | Question it answers | Constrained? | Examples |
|---|---|---|---|
| `type` | "What is this entity?" | Unconstrained (graph-model vocabulary) | conversation, paper, vulnerability, person |
| `kind` | "What kind of content is this?" | Unconstrained (standard vocabulary) | learning, breakdown, decision, reference |
| `act` | "What speech act is this?" | Constrained | assertion, commitment, request, declaration |
| `topic` | "What is this about?" | Unconstrained | auth, database, testing |

A note can have all four: `type=conversation kind=decision act=declaration topic=auth`

## Impact locations

### Tag definition (create new, update existing)

| File | Change |
|---|---|
| `keep/data/system/tag-type.md` | Remove content-kind values table. Redefine as entity-type only. Update prompt section and examples. |
| `keep/data/system/tag-kind.md` | **New file.** Content-kind tag definition with values table, prompt section, and examples. Migrated from tag-type.md. |

### Meta-docs (tag filters)

| File | Change |
|---|---|
| `keep/data/system/meta-learnings.md` | `{type: learning}` -> `{kind: learning}`, `{type: breakdown}` -> `{kind: breakdown}`, `{type: gotcha}` -> `{kind: gotcha}` |

### System docs (examples and descriptions)

| File | Change |
|---|---|
| `keep/data/system/tag-act.md` | Update orthogonality examples (`type=learning` -> `kind=learning`) |
| `keep/data/system/tag-topic.md` | Update CLI examples |
| `keep/data/system/conversations.md` | Update examples: `type=possibility` -> `kind=possibility`, `type=breakdown` -> `kind=breakdown` |
| `keep/data/system/domains.md` | Update example: `type=breakdown` -> `kind=breakdown` |
| `keep/data/system/library.md` | Update example: `type=teaching` -> `kind=teaching` |
| `keep/data/system/now.md` | Update example: `type=teaching` -> `kind=teaching` |
| `keep/data/system/prompt-agent-system.md` | Update example: `type=learning` -> `kind=learning` |

### User docs

| File | Change |
|---|---|
| `docs/TAGGING.md` | Update tag reference table; split type/kind descriptions |
| `docs/META-TAGS.md` | Update examples: `type=learning`, `type=breakdown` -> `kind=` |
| `docs/KEEP-PUT.md` | Update example: `type=reference` -> `kind=reference` |
| `docs/PROMPTS.md` | Clarify type= match rules use entity-type values; mention kind |
| `docs/API-SCHEMA.md` | Update tag reference table |
| `docs/AGENT-GUIDE.md` | Update examples throughout |
| `docs/KEEP-GET.md` | Update example: `type=learning` -> `kind=learning` |
| `docs/KEEP-MCP.md` | Update example |
| `docs/OPENCLAW-INTEGRATION.md` | Update example |

### Tests

| File | Change |
|---|---|
| `tests/test_meta_resolution.py` | Update test data and expectations for `kind=` |
| `tests/test_core.py` | Update parse test data |

### Design docs (historical, update for accuracy)

| File | Change |
|---|---|
| `later/design/design-context-component-cache.md` | Update meta-learnings reference |
| `later/design/design-find-cache-filtered-invalidation.md` | Update filter example |

### Python code

The migration itself is implemented in Python (see below). The tag vocabulary
is entirely data-driven through system docs and meta-docs — no other Python
code hard-codes content-kind values.

### Prompt match rules (NO CHANGE)

The prompt-analyze files (`prompt-analyze-conversation.md`,
`prompt-analyze-paper.md`) use `type=conversation` and `type=paper` — these
are entity-type values and remain correct after the split.

## Migration of existing data

**Chosen: daemon startup migration (option 1).**

### Implementation

**Config flag**: `StoreConfig.type_to_kind_migrated: bool = False`
(`keep/config.py`). Set to `True` after successful migration; persisted via
`save_config()`.

**Migration method**: `Keeper._migrate_type_to_kind(doc_coll)` in
`keep/api.py`. Follows the same pattern as `_migrate_labeled_ref_format`:

1. Iterates all documents via `list_ids()` → `get()`
2. For each doc, calls `_retag_type_to_kind(tags)` which:
   - Reads `type` tag values
   - Splits into content-kind values (in `_TYPE_TO_KIND_VALUES` frozenset)
     and entity-type values (everything else)
   - Moves content-kind values to `kind`, merging with any existing `kind`
     values (hermes already writes `kind=compression-snapshot` etc.)
   - Removes content-kind values from `type`; if no entity-type values
     remain, removes `type` entirely
   - Returns `None` if no change needed (skip the write)
3. Updates both DocumentStore (`patch_head_tags`) and ChromaStore
   (`_rewrite_index_tags_without_timestamp`) for changed docs
4. Repeats for all versions and parts
5. Returns stats dict `{documents, versions, parts}`

**Entry point**: `_run_type_to_kind_migration(doc_coll)` — checks config
flag, runs migration, persists flag. Called from
`_run_deferred_startup_maintenance()` after the tag marker check.

**Content-kind values** (the set that moves to `kind`):
`learning`, `breakdown`, `gotcha`, `reference`, `teaching`, `meeting`,
`pattern`, `possibility`, `decision`

### Idempotency

Safe to re-run: `_retag_type_to_kind` returns `None` for notes that already
have no content-kind values in `type`. The config flag prevents unnecessary
full scans on subsequent startups.

## Future: conditioning `act` on `type`

Once `type` reliably means entity-type, the `act` (speech-act) classifier
can be conditioned to only run on `type=conversation` notes. This is a
separate change but enabled by this split. The mechanism (option C from the
initial analysis) would be an `_applies_when:` field in tag-act.md's
frontmatter, evaluated by TagClassifier before classification.

## Phase 1 Status

- [x] Create `tag-kind.md` with content-kind vocabulary
- [x] Update `tag-type.md` to entity-type only
- [x] Update `meta-learnings.md` filters
- [x] Update all system docs and user docs (including library frontmatter)
- [x] Update tests (test_core.py, test_meta_resolution.py)
- [x] Implement daemon startup migration

---

# Phase 2: Unified Item Context and CEL Conditions

## Motivation

Three systems evaluate conditions against item properties, each using a
different item representation:

1. **State-doc CEL** (`when:` rules) — purpose-built `item` dict with
   `has_*` booleans, no `id`, no `summary`, no timestamps
2. **Prompt matching** — simple `key=value` DSL against tags only
3. **Edge tag applicability** — no condition mechanism at all

Meanwhile, the item's *expressed* shape (get output, export frontmatter) is
different from the CEL shape. Writing a `_when` condition should feel natural
to someone who has seen items in output — the internal and expressed shapes
must converge.

## Unified Item Context

A single `item` dict shape used everywhere: CEL evaluation, prompt
conditions, edge conditions, tag classifier conditions.

```python
item = {
    # Identity
    "id": str,                    # note ID

    # Content metadata
    "summary": str,               # summary text ("" if unset)
    "content_length": int,        # length of content in chars
    "content_type": str,          # MIME type ("" if unset)
    "uri": str,                   # source URI ("" if none)

    # Timestamps
    "created": str,               # ISO timestamp
    "updated": str,               # ISO timestamp
    "accessed": str,              # ISO timestamp

    # All tags
    "tags": dict[str, Any],       # full tag map including system tags
}
```

### What this eliminates

| Old field | Replacement in CEL |
|---|---|
| `item.has_uri` | `item.uri != ""` |
| `item.has_summary` | `item.summary != ""` |
| `item.has_content` | `item.content_length > 0` |
| `item.is_system_note` | `item.id.startsWith(".")` |
| `item.has_media_content` | dropped — use `item.content_type` tests |
| `params.item_id` | `item.id` |

### Construction

A single builder function `build_item_context(doc, content="")` produces
this dict from either:
- A stored `Item`/document record (for prompt matching, edge conditions)
- Raw write params (for after-write state docs, as today)

The builder lives in one place and is the single source of truth for the
item schema.

## CEL as the Unified Condition Language

### `_when` on tagdocs

Tagdocs (`.tag/{name}`) gain an optional `_when:` field in frontmatter.
Evaluated against the **source note's** item context.

```yaml
# .tag/from
tags:
  _inverse: from_to
  _when: "'email' in item.tags.type"
```

Edge materialization checks `_when` before creating the edge. For inverse
edges, the condition is on the *source* node (the one with the tag), not
the target — if the source doesn't meet the condition, the edge was never
created and won't appear in either direction.

### `_when` on tag classifiers

Constrained tag specs gain `_when:` to control when classification applies:

```yaml
# .tag/act
tags:
  _constrained: "true"
  _when: "'conversation' in item.tags.type"
```

TagClassifier evaluates `_when` before running classification for that tag.

### `_when` on prompt docs

Prompt docs (`.prompt/{prefix}/*`) replace the `key=value` match-rule DSL
with a `_when:` frontmatter field:

```yaml
# .prompt/analyze/conversation
tags:
  category: system
  context: prompt
  _when: "'conversation' in item.tags.type"
```

`_resolve_prompt_doc()` evaluates `_when` instead of parsing match rules.
Specificity is no longer needed — if multiple prompts match, the most
specific `_when` (by convention) wins, or an explicit `_priority: N` field
breaks ties.

**Backwards compatibility**: existing `key=value` match rules in the doc
body continue to work during a transition period. If `_when:` is present
in frontmatter, it takes precedence.

## State-doc CEL Expression Rewrites

All existing `when:` expressions that reference `item.*` must be updated
to match the new unified schema.

### after-write rules

| File | Current | New |
|---|---|---|
| `state-after-write.md` (summarize) | `item.content_length > params.max_summary_length && !item.has_summary` | `item.content_length > params.max_summary_length && item.summary == ""` |
| `state-after-write.md` (describe) | `item.has_uri && item.has_media_content && system.has_media_provider` | `item.uri.startsWith("file://") && (item.content_type.startsWith("image/") \|\| item.content_type.startsWith("audio/") \|\| item.content_type.startsWith("video/")) && system.has_media_provider` |
| `state-after-write/links.md` | `!item.is_system_note && item.has_content && (item.content_type == 'text/markdown' \|\| ...)` | `!item.id.startsWith(".") && item.content_length > 0 && (item.content_type == 'text/markdown' \|\| ...)` |
| `state-after-write/analyze.md` | `!item.is_system_note && (item.content_length > 500 \|\| item.has_uri) && !(has(item.tags._source) && item.tags._source == 'link') && ...` | `!item.id.startsWith(".") && (item.content_length > 500 \|\| item.uri != "") && !(has(item.tags._source) && item.tags._source == 'link') && ...` |
| `state-after-write/tag.md` | `!item.is_system_note && item.has_content` | `!item.id.startsWith(".") && item.content_length > 0` |
| `state-after-write/duplicates.md` | `!item.is_system_note && item.has_content` | `!item.id.startsWith(".") && item.content_length > 0` |
| `state-after-write/resolve-stubs.md` | `item.has_uri && !item.is_system_note && !(has(item.tags._source) && ...)` | `item.uri != "" && !item.id.startsWith(".") && !(has(item.tags._source) && ...)` |
| `state-after-write/ocr.md` | `'_ocr_pages' in item.tags && item.has_uri` | `'_ocr_pages' in item.tags && item.uri != ""` |

### tag-doc rules

| File | Current | New |
|---|---|---|
| `tag-references.md` | `item.content_type == 'text/markdown' \|\| item.content_type == 'text/plain'` | unchanged (already uses unified field name) |

### Non-item rules (NO CHANGE)

These expressions don't reference `item.*` and are unaffected:

- `state-query-resolve.md` — uses `params.*`, `search.*`
- `state-query-branch.md` — uses `params.*`, `search.*`, `budget.*`
- `state-query-explore.md` — uses `params.*`, `search.*`, `budget.*`
- `state-find-deep.md` — uses `params.*`, `search.*`
- `state-memory-search.md` — uses `params.*`
- `state-get.md` — uses `params.*`
- `state-get/openclaw.md` — uses `params.*`
- `meta-genre.md`, `meta-album.md`, `meta-artist.md` — use `params.*`

## Design Decisions

### Verbose CEL over convenience booleans

`has_media_content` is replaced by the full CEL expression testing
`item.uri.startsWith("file://")` and content_type prefixes. The state doc
is the source of truth for "when does this action run" — that logic should
be visible in the expression, not hidden behind a function name.

### `system.*` namespace retained

`system.has_media_provider` is the only field. It's config/environment
state, not item state. Kept as-is — harmless, and the namespace is ready
if more capabilities are added (e.g. `system.has_content_extractor`).

### `params.*` retained

`params.item_id`, `params.max_summary_length`, etc. remain available
alongside `item.*`. The `item` dict is promoted into the eval context as a
top-level key; `params` continues to carry action-specific parameters and
template values for `with:` blocks.

## Phase 2 Status

- [x] Create `build_item_context()` builder function
- [x] Update `_background_processing.py` to use builder instead of inline dict
- [x] Add tests: verify builder output matches expected schema
- [x] Add tests: verify each state-doc `when:` expression evaluates correctly
      against the new item context (regression coverage for rewrite)
- [x] Rewrite all `item.*` CEL expressions in state docs
- [x] Remove dead code (`_has_local_describable_media`, `_DESCRIBABLE_MEDIA_PREFIXES`)

---

# Phase 3: Conditional Edge Tags

## Motivation

Edge tags (tag keys with `_inverse` in their `.tag/{key}` definition)
unconditionally materialize edges for every note that carries the tag.
But some tag keys are edge-bearing only in certain contexts:

- `from` is an email address in `type=email` content, but a date or
  location in other content
- `speaker` makes sense in `type=conversation`, not in `type=paper`
- `author` applies to documents, not to conversation turns

Without conditions, either (a) the tag is only used in appropriate
contexts (fragile — depends on analyzer discipline), or (b) spurious
edges appear in the graph.

## Design

### `_when` field on tagdocs

A tagdoc with `_inverse` gains an optional `_when` tag in frontmatter.
The value is a CEL expression evaluated against the **source note's**
unified item context.

```yaml
# .tag/from
tags:
  _inverse: from_to
  _when: "'email' in item.tags.type || 'message/rfc822' == item.content_type"
```

If `_when` is absent, the edge is unconditional (current behaviour).
If `_when` evaluates to false for a source note, no edge is created
and no target is auto-vivified.

### Condition semantics

- Conditions apply to the **source node** (the one carrying the tag).
- For **inverse edges** (looking up "who points at me"): no separate
  condition check is needed. If the source didn't meet `_when` at
  write time, the edge row was never inserted, so it won't appear.
- For **backfill** (re-scanning all versions after `_inverse` is
  added or changed): the same `_when` is evaluated against each
  version's tags to decide whether to create version-edge rows.

### CEL context for edge evaluation

The item context is built via `build_item_context()` from the source
note's stored document record. At the point of edge materialization
(`_restore_current_edges_without_backfill`), the source note's tags
are available as `merged_tags` and its `id` is known. The builder
needs:

```python
build_item_context(
    id=source_id,
    tags=merged_tags,
    summary=source_doc.summary,       # from document_store.get()
    content_length=...,               # from content or tags
    content_type=merged_tags.get("_content_type", ""),
    uri=merged_tags.get("_source_uri", ""),
)
```

Note: `content_length` is not stored on the document record and isn't
available at edge materialization time (content is not loaded). In edge
and prompt contexts, `content_length` is set to `None` (not zero).

If a `_when` expression accidentally references `item.content_length`
in an edge context, the CEL evaluator will raise (e.g. `None > 500`
is a type error), `_eval_predicate` will log a warning with the full
expression source, and the predicate returns false (edge skipped).
This is the right failure mode: visible, safe, and actionable.

### Compiled predicate caching

The tagdoc cache (`_tagdoc_cache`) already stores tagdoc tags per key.
Add a parallel `_tagdoc_when_cache: dict[str, Any | None]` that stores
the compiled CEL program (or `None` if no `_when`). Compiled once on
first access, reused for all subsequent edge evaluations. Cache is
cleared alongside `_tagdoc_cache` on tagdoc writes.

## Implementation

### 1. Parse and cache `_when` from tagdocs

**File:** `keep/api.py` — `_restore_current_edges_without_backfill()`

Current code (line ~544):
```python
if key not in self._tagdoc_cache:
    parent = self._document_store.get(doc_coll, f".tag/{key}")
    self._tagdoc_cache[key] = parent.tags if parent else None
td_tags = self._tagdoc_cache[key]
if td_tags is None or not td_tags.get("_inverse"):
    continue
```

Add after the `_inverse` lookup:
```python
# Check _when condition on the tagdoc
if key not in self._tagdoc_when_cache:
    when_source = td_tags.get("_when", "")
    if when_source:
        self._tagdoc_when_cache[key] = _compile_predicate(when_source)
    else:
        self._tagdoc_when_cache[key] = None
```

### 2. Evaluate `_when` during edge materialization

**File:** `keep/api.py` — `_restore_current_edges_without_backfill()`

After the `_inverse` check and `_when` cache lookup, before iterating
tag values:

```python
when_prog = self._tagdoc_when_cache.get(key)
if when_prog is not None:
    item_ctx = build_item_context(
        id=id,
        tags=merged_tags,
        content_type=merged_tags.get("_content_type", ""),
        uri=merged_tags.get("_source_uri", ""),
    )
    if not _eval_predicate(when_prog, {"item": item_ctx}):
        continue  # skip this edge tag entirely
```

If `_when` is `None` (absent from tagdoc), no condition check — edges
materialize unconditionally as today.

### 3. Evaluate `_when` during version-edge backfill

**File:** `keep/document_store.py` —
`backfill_version_edges_for_predicate()`

This method iterates all versions and creates version-edge rows. It
currently has no access to tagdoc metadata — it receives only
`predicate` and `inverse` strings.

**Change:** Add an optional `when_source: str = ""` parameter. If
provided, compile the CEL predicate and evaluate it against each
version's tags before inserting the edge row.

```python
def backfill_version_edges_for_predicate(
    self, collection: str, predicate: str, inverse: str,
    *, when_source: str = "",
) -> int:
```

The caller (`_enqueue_edges_backfill` or the backfill task processor)
passes the `_when` value from the tagdoc.

### 4. Cache invalidation

**File:** `keep/api.py`

When a tagdoc is written (`_process_tagdoc_inverse_change`), clear
the corresponding entry from `_tagdoc_when_cache` alongside the
existing `_tagdoc_cache` invalidation:

```python
self._tagdoc_cache.pop(tag_key, None)
self._tagdoc_when_cache.pop(tag_key, None)
```

Also clear `_tagdoc_when_cache` wherever `_tagdoc_cache.clear()` is
called (system doc migration).

### 5. No change to edge deletion

When a source note is deleted, its edges are deleted by source_id —
the condition doesn't matter. When a target is deleted, its inverse
edges are deleted by target_id. No condition check needed in either
case.

### 6. No change to `get_inverse_edges` query

The SQLite `edges` table query is unchanged. If an edge wasn't created
(because `_when` was false), the row doesn't exist, so it won't be
returned. No runtime condition check on reads.

## Tests

### Unit tests (test_edges.py)

```
test_conditional_edge_created_when_condition_met
    - Create .tag/from with _inverse=from_to and _when="'email' in item.tags.type"
    - Put a note with type=email and from=alice
    - Verify edge row exists, inverse visible on target

test_conditional_edge_skipped_when_condition_not_met
    - Same tagdoc setup
    - Put a note WITHOUT type=email, but with from=2026-01-01
    - Verify NO edge row, no auto-vivification of target

test_unconditional_edge_still_works
    - Create .tag/speaker with _inverse=said, NO _when
    - Put a note with speaker=nate
    - Verify edge exists (backward compat)

test_conditional_edge_inverse_not_visible
    - Create conditional edge tagdoc
    - Put note that doesn't meet condition, with from=alice
    - get_context("alice") → no "from_to" in edges

test_conditional_edge_condition_on_content_type
    - _when: "item.content_type == 'message/rfc822'"
    - Put with content_type=message/rfc822 → edge created
    - Put with content_type=text/markdown → no edge

test_condition_with_multiple_matching_notes
    - Two notes with same tag key; one meets condition, one doesn't
    - Verify edge exists only for the matching note

test_tagdoc_when_change_clears_cache
    - Create tagdoc without _when, put note → edge created
    - Update tagdoc to add _when, put another note that doesn't match
    - Verify second note has no edge
```

### Integration tests

```
test_bundled_from_tagdoc_conditional_edge
    - If .tag/from gets _when in a future release, verify it works
      end-to-end with system doc migration

test_backfill_respects_when_condition
    - Create notes with tag values
    - Add _inverse + _when to the tagdoc after the fact
    - Trigger backfill
    - Verify only matching notes have version-edge rows
```

## Fix: inverse edges queryable via `find`

### Current asymmetry

Outbound edges are stored as regular tags on the source document, so
`keep find -t speaker=nate` works — Chroma and FTS5 both index the tag.

Inverse edges (`said`, `cited_by`, `referenced_by`, etc.) exist only
in the `edges` SQLite table. They are only visible through `get` on
the target. `keep find -t cited_by=source_id` returns nothing.

This is documented as a known limitation (EDGE-TAGS.md:150) but should
be fixed as part of the edge system work.

### Design

Extend `_find_direct()` to detect inverse-edge tag keys in the `tags`
filter and translate them to edges-table queries.

**Detection**: when processing a tag filter like `{cited_by: source_id}`,
look up `.tag/cited_by`. If it exists and has `_inverse` (meaning it
*is* an inverse predicate), the filter is an inverse-edge query.

**Query path**: use a new `DocumentStore.find_by_inverse_edge()` method:

```sql
SELECT target_id FROM edges
WHERE collection = ? AND inverse = ? AND source_id = ?
```

This query is covered by the existing `idx_edges_target` index
(which indexes `target_id, collection, inverse, created`). For the
reverse lookup by `inverse + source_id`, a new index may be needed:

```sql
CREATE INDEX idx_edges_inverse_source
ON edges (collection, inverse, source_id)
```

**Integration into find**: inverse-edge results are pre-filtered IDs.
The find pipeline intersects these with the rest of the tag/query
results, or uses them as the initial candidate set when no semantic
query is provided.

**Alternatively** — for `find` calls with *only* inverse-edge tags and
no semantic query (like `keep find -t cited_by=X`), return the edge
targets directly without going through the search pipeline.

### What changes

| Component | Change |
|---|---|
| `DocumentStore` | Add `find_by_inverse_edge(collection, inverse, source_id)` |
| `DocumentStore` | Add index `idx_edges_inverse_source` (schema migration) |
| `Keeper._find_direct` | Detect inverse-edge keys, split tag filter |
| `docs/EDGE-TAGS.md` | Remove the "only visible through get" caveat |

### Tests

```
test_find_by_inverse_edge_tag
    - Create .tag/cites with _inverse=cited_by
    - Source A cites target B
    - find(tags={cited_by: A}) → returns B

test_find_inverse_edge_with_semantic_query
    - Same setup, add query="something"
    - Results intersected: only B if it also matches the query

test_find_inverse_edge_nonexistent_predicate
    - find(tags={not_a_real_inverse: X}) → treated as regular tag,
      returns nothing (no false positives)
```

## Phase 3 Status

- [x] Add `_tagdoc_when_cache` dict to Keeper.__init__
- [x] Parse `_when` from tagdoc tags during edge materialization
- [x] Evaluate `_when` in `_process_edge_tags` (write-time) and
      `_restore_current_edges_without_backfill` (migration)
- [x] Pass `when_source` through to backfill
- [x] Cache invalidation on tagdoc write
- [x] Add `find_by_inverse_edge` to DocumentStore + index migration (v16)
- [x] Detect inverse-edge keys in `_find_direct` tag filter
- [x] Tests: conditional edge created/skipped (7 tests)
- [x] Tests: find by inverse edge tag (2 tests)
- [x] Tests: DocumentStore.find_by_inverse_edge (2 tests)
- [x] Update EDGE-TAGS.md: conditional edges + inverse find docs

---

# Phase 4: Automatic `type=conversation` on captured messages

## Motivation

Conversation-specific prompts (`prompt-analyze-conversation.md`,
`prompt-summarize-conversation.md`) match on `type=conversation`.
Today, no conversation capture source sets this tag automatically.
User/assistant messages from Hermes, OpenClaw, and IDE hooks arrive
without `type`, so they get the default analysis/summarization
prompts — missing conversation-specific extraction like speaker
attribution and speech-act classification.

## Distinguishing principle

The rule is: **if the system is automatically capturing what a user
or assistant said as part of a dialogue, that's a conversation.** If
a user or agent is deliberately writing a note via tool use, it's
not — the caller chooses its own type/kind.

The distinction is about **who initiated the write**, not the item
ID. `keep now "working on auth"` is deliberate intention-setting.
`UserPromptSubmit` automatically capturing the user's prompt is
conversation capture. Same `now` doc, different semantics.

## Scope

**Should get `type=conversation`:**
- **Hermes `sync_turn()`** — the gateway automatically captures
  user/assistant message pairs as versioned items on every turn.
  This is the primary conversation capture path for Hermes-managed
  agent sessions.
- **OpenClaw `ingest()` / `ingestBatch()`** — the context engine
  automatically captures individual messages as they arrive from the
  gateway. Each message is a conversation turn.
- **Claude Code `UserPromptSubmit` hook** — the harness
  automatically fires on every user prompt submission. The hook
  command captures the prompt text. This is automatic conversation
  capture, not deliberate tool use.
- **Kiro `promptSubmit` hook** — same pattern as Claude Code.

**Should NOT get `type=conversation`:**
- `keep now "working on auth"` — deliberate intention-setting by
  user or agent.
- `keep put "some insight" -t kind=learning` — deliberate note.
- `keep_flow(state="put", ...)` — deliberate agent tool use.
- Hermes `on_pre_compress()` — operational (`kind=compression-snapshot`).
- Hermes `on_delegation()` — operational (`kind=delegation`).
- Hermes `on_memory_write()` — memory mirror (`source=hermes-builtin`).
- OpenClaw `afterTurn()` compaction summaries — operational.
- Codex — only protocol block, no conversation hooks.

## Changes

### 1. Hermes provider (`keep/hermes/provider.py`)

**`sync_turn()`** — add `type: "conversation"` to session tags:

```python
# In _build_session_tags() or sync_turn() tag construction
tags["type"] = "conversation"
```

This is the only Hermes method that writes actual conversation turns.
The other methods (`on_pre_compress`, `on_delegation`,
`on_memory_write`) should NOT set `type=conversation`.

### 2. OpenClaw context engine (`keep/data/openclaw-plugin/src/index.ts`)

**`ingest()` and `ingestBatch()`** — add `type: "conversation"` and
`source: "openclaw"` to tags:

```typescript
// In sessionTags() or at the ingest call sites
tags.type = "conversation";
tags.source = "openclaw";
```

**`afterTurn()` compaction summaries** — no change (already has
`type: compaction-summary`).

### 3. IDE hooks (`keep/integrations.py`, `keep/data/kiro-hooks/`)

The hook commands themselves set `type=conversation` in their
invocation, since these are automatic conversation captures:

**Claude Code** (`integrations.py` CLAUDE_CODE_HOOKS):
```python
# UserPromptSubmit hook command — add -t type=conversation
"command": "keep now 'User prompt: ${.prompt|text}' --truncate -t type=conversation 2>/dev/null || true",
```

**Kiro** (`keep-prompt.kiro.hook`):
```json
"command": "printf 'User prompt: %s' \"$USER_PROMPT\" | keep now -t type=conversation 2>/dev/null || true"
```

This keeps the tagging at the source (the hook definition) rather
than injecting it in the write path. The `now` doc itself is not
inherently conversational — it's the hook's automatic capture that
makes it conversation content.

### Source tag consistency

Currently Hermes uses `source: "hermes"` (no underscore) while
the system convention is `_source` (with underscore). This is
intentional — `_source` is a system-managed provenance tag set
during write (`inline`, `uri`, `link`, `auto-vivify`). The
user-facing `source` tag is a different concept: "what integration
produced this content."

No change to `_source` — the `source` tag stays as a user-facing
attribution. OpenClaw should add `source: "openclaw"` for parity
with Hermes.

### Prompt matching (already works)

`prompt-analyze-conversation.md` has match rule `type=conversation`.
`prompt-summarize-conversation.md` has match rule `type=conversation`.
Once the sources set this tag, conversation-specific prompts will
activate automatically. No prompt changes needed.

### Act classifier conditioning (future, enabled by this)

Once conversation items are reliably tagged `type=conversation`,
the `act` (speech-act) tag classifier can be conditioned to only
run on conversations:

```yaml
# .tag/act
tags:
  _constrained: "true"
  _when: "'conversation' in item.tags.type"
```

This prevents speech-act classification on non-conversation content
(papers, references, etc.) where it produces noise. This is a
separate change using the `_when` mechanism from Phase 3.

## What does NOT change

- **`keep now`** (deliberate) — user/agent writes intentions
  or status updates. No automatic `type` injection.
- **`keep put`** — user chooses their own tags.
- **`keep_flow(state="put")`** — agent chooses its own tags.
- **File/URL indexing** — `_source: uri` items get their type
  from content analysis, not from the capture path.

## Tests

```
test_hermes_sync_turn_sets_type_conversation
    - Call sync_turn with user/assistant content
    - Verify stored item has type=conversation

test_hermes_on_delegation_does_not_set_type_conversation
    - Call on_delegation
    - Verify stored item does NOT have type=conversation

test_hermes_on_memory_write_does_not_set_type_conversation
    - Call on_memory_write
    - Verify stored item does NOT have type=conversation

test_claude_code_hook_command_includes_type_conversation
    - Verify CLAUDE_CODE_HOOKS["UserPromptSubmit"] command
      contains "-t type=conversation"

test_kiro_hook_command_includes_type_conversation
    - Verify keep-prompt.kiro.hook command contains
      "-t type=conversation"

test_openclaw_ingest_sets_type_conversation
    - (TypeScript test in openclaw plugin)
```

## Phase 4 Status

- [x] Add `type=conversation` in Hermes `sync_turn()` tags
- [x] Add `-t type=conversation` to Claude Code `UserPromptSubmit`
      hook command (also changed prefix from "User prompt:" to "User:")
- [x] Add `-t type=conversation` to Kiro `promptSubmit` hook command
      (also changed prefix from "User prompt:" to "User:")
- [x] Add `type=conversation` + `source=openclaw` in OpenClaw
      `ingest()` / `ingestBatch()` / `afterTurn()` message re-ingest
- [x] Tests: Hermes sync_turn sets type=conversation (1 test)
- [x] Tests: delegation/compress do NOT set type=conversation (2 tests)
- [x] Tests: hook commands include type=conversation (2 tests)

## Phase 5 Status — `_when` on prompts and tag classifier

- [x] Add `_when` evaluation to `_resolve_prompt_doc()` — CEL
      condition checked per prompt doc; passing `_when` boosts
      specificity by 1 over bare defaults
- [x] Add `_when` filtering to tag classifier — specs carry `_when`
      from `load_tag_specs`; `_filter_specs_by_when` evaluates against
      item context in `classify_parts_with_specs`, `auto_tag`, and
      both `analyze` paths (action + legacy api.py)
- [x] Add `_when: "'conversation' in item.tags.type"` to `.tag/act`
      tagdoc — speech-act classification now only runs on conversations
- [x] Tests: prompt doc `_when` matching (2 tests)
- [x] Tests: tag spec `_when` filtering (3 tests)
