# Design: Split `type` into `type` (entity-type) + `kind` (content-kind)

Status: **implemented** (v0.140.0)

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

## Status

- [x] Create `tag-kind.md` with content-kind vocabulary
- [x] Update `tag-type.md` to entity-type only
- [x] Update `meta-learnings.md` filters
- [x] Update all system docs and user docs (including library frontmatter)
- [x] Update tests (test_core.py, test_meta_resolution.py)
- [x] Implement daemon startup migration
- [ ] (Future) Condition `act` classifier on `type=conversation`
