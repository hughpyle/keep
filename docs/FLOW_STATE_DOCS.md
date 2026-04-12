# Built-in State Docs

State docs are YAML documents stored as `.state/*` notes that drive keep's
processing flows. Fifteen ship by default: simple operation wrappers, the
processing pipeline, the iterative query state machine, and a few specialized
flows for memory tools and supernode review. Each is loaded from disk on
first use and can be edited in the store.

To view the current state docs: `keep list .state --all`
To reset to defaults: `keep config --reset-system-docs`
To view the state diagram: `keep config --state-diagram`

The bundled set:

| State doc | Mode | Path | Purpose |
|-----------|------|------|---------|
| `.state/after-write` | `match: all` | background | Post-write processing pipeline |
| `.state/get` | `match: all` | sync | Display-context assembly |
| `.state/find-deep` | `match: sequence` | sync | Search + edge traversal |
| `.state/list` | `match: sequence` | sync | Plain enumeration with filters |
| `.state/list_versions` | `match: sequence` | sync | Version history listing |
| `.state/memory-search` | `match: all` | sync | Scoped search for memory tools |
| `.state/query-resolve` | `match: sequence` | sync | Iterative query entry point |
| `.state/query-branch` | `match: all` | sync | Faceted search disambiguation |
| `.state/query-explore` | `match: sequence` | sync | Wider exploratory search |
| `.state/put` | wrapper | sync | Wraps `put` action |
| `.state/tag` | wrapper | sync | Wraps `tag` action |
| `.state/delete` | wrapper | sync | Wraps `delete` action |
| `.state/move` | wrapper | sync | Wraps `move` action |
| `.state/stats` | wrapper | sync | Store profiling for query planning |
| `.state/review-supernodes` | `match: sequence` | background | Supernode factsheet review |

---

## .state/after-write

**Trigger:** Every `put()` call.
**Mode:** `match: all` — all matching rules fire in parallel.
**Path:** Background (returns immediately, work runs async).

Runs post-write processing on new or updated items. The base doc defines
core rules; additional rules are loaded from builtin fragments at
`.state/after-write/*` (see [Fragments](#fragments) below).

**Base rules:**

| Rule | Condition | Action |
|------|-----------|--------|
| `summary` | Content exceeds max summary length and no summary exists | `summarize` |
| `described` | Item has a URI, local describable media content, and a media provider configured | `describe` |

**Builtin fragments** (`keep/data/system/state-after-write/`):

| Fragment | Rule id | Condition (CEL) | Action |
|----------|---------|-----------------|--------|
| `analyze` | `analyzed` | `!item.is_system_note` | `analyze` (decompose into parts) |
| `duplicates` | `find-duplicates` | `!item.is_system_note && item.has_content` | `resolve_duplicates` (link identical content via edges) |
| `links` | `linked` | `!item.is_system_note && item.has_content && content_type ∈ {markdown, plain, html, message/rfc822, pdf, docx, pptx}` | `extract_links` (wiki/markdown links, URLs, emails, structured doc links → `references` edges) |
| `ocr` | `extracted` | `'_ocr_pages' in item.tags && item.has_uri` | `ocr` |
| `resolve-stubs` | `resolve_stubs` | `item.has_uri && !item.is_system_note && item.tags._source != 'link'` | `resolve_stubs` (fetch URI for stub items) |
| `tag` | `tagged` | `!item.is_system_note && item.has_content` | `auto_tag` (classify against `.tag/*` specs) |

System notes (IDs starting with `.`) skip every fragment that gates on
`!item.is_system_note`, which keeps the pipeline from recursively processing
its own state docs, prompt docs, and tag descriptions. Fragments can be
disabled individually (see [Extending state docs](#extending-state-docs)
below).

The `links` fragment handles every text-bearing content type, not just
markdown — it covers plain text, HTML, RFC 822 email, PDF, DOCX, and PPTX as
well, so link extraction works on indexed documents from many sources. Provider-
extracted structured links (such as PDF annotations) are passed through the
after-write flow to `extract_links`, and email targets are normalized to bare
email-address note IDs. The
`resolve-stubs` fragment runs for any URI-backed item that isn't a `link`-
sourced stub, which includes both edge-target stubs and other URI items
that came in as placeholders.

Remote HTTP(S) binary fetches do not trigger `describe`. The fetcher may
use temporary files during extraction, but those files are not durable once
the asynchronous `after-write` flow begins.

---

## .state/get

**Trigger:** `get()` and `now()` calls.
**Mode:** `match: all` — all queries run in parallel.
**Path:** Synchronous (completes before returning to caller).

Assembles the display context shown when you retrieve a note. Three parallel
queries:

| Rule | Action | Purpose |
|------|--------|---------|
| `similar` | `find` (by similarity) | Semantically related items |
| `parts` | `find` (by prefix) | Structural parts from `analyze` |
| `meta` | `resolve_meta` | Meta-doc sections (learnings, todos, etc.) |

**Fragments:** `state-get/openclaw.md` adds two extra rules used by the
OpenClaw integration — a query-based `search` rule (replaces `similar` when
the agent prompt is present) and a `session` rule that fetches the current
session item. Inserted before the base `similar` rule with complementary
`when` guards so exactly one of `search`/`similar` fires.

---

## .state/find-deep

**Trigger:** `find()` with `--deep` flag.
**Mode:** `match: sequence` — rules evaluate top-to-bottom.
**Path:** Synchronous.

Searches, then follows edges from results to discover related items.

1. Run semantic search with the query
2. If no results, return immediately
3. Traverse edges from search hits to find connected items
4. Return combined results

---

## .state/list

**Trigger:** `keep list` CLI, `kp.list_items()` Python API.
**Mode:** `match: sequence`.
**Path:** Synchronous.

Plain enumeration of items by prefix, tags, or date range. Distinct from
`query-resolve` — no semantic search, no scoring, just listing in tag order.

| Param | Description |
|-------|-------------|
| `prefix` | ID prefix or glob (e.g. `.tag/`, `session-*`) |
| `tags` | Tag key=value filter (AND across keys) |
| `tag_keys` | Filter by presence of tag keys (any value) |
| `since` / `until` | Time filters |
| `order_by` | `updated`, `accessed`, `created`, or `id` |
| `include_hidden` | Include system notes (dot-prefix IDs) |
| `limit` | Maximum results |

**Output:** `{"results": [...], "count": N}`

---

## .state/list_versions

**Trigger:** `keep get --history`, `kp.list_versions()`.
**Mode:** `match: sequence`.
**Path:** Synchronous.

Returns the version history for a single item.

| Param | Description |
|-------|-------------|
| `id` (or `item_id`) | Item to list versions for |
| `limit` | Maximum versions to return |

**Output:** `{"versions": [...]}`

---

## .state/memory-search

**Trigger:** OpenClaw `memory_search` tool.
**Mode:** `match: all`.
**Path:** Synchronous.

Scope-constrained semantic search used by the OpenClaw integration's
`memory_search` MCP tool. Wraps `find` with a forced `scope` parameter so
results are constrained to memory-file paths (`MEMORY.md`, `memory/*.md`).

| Param | Description |
|-------|-------------|
| `query` | Search query |
| `scope` | ID glob pattern to constrain results |
| `limit` | Maximum results |

**Output:** `{"results": [...]}`

---

## .state/query-resolve

> Thresholds for query resolution are configurable but not yet tuned
> against real query patterns. Results are functional but may route
> suboptimally in edge cases.

**Trigger:** Internal query resolution (multi-step search).
**Mode:** `match: sequence` — first matching rule wins.
**Path:** Synchronous, with tick budget.

The entry point for iterative query refinement. Searches, evaluates result
quality, and routes:

| Condition | Action |
|-----------|--------|
| High margin (clear winner) | Return done |
| Strong lineage signal | Re-search with dominant lineage tags, loop back |
| Low margin or high entropy | Transition to `query-branch` |
| Low entropy (tight cluster) | Widen search, loop back |
| No strong signal (fall-through) | Transition to `query-explore` |

**Signals used:** `search.margin`, `search.entropy`, `search.lineage_strong`,
`search.dominant_lineage_tags`, `search.top_facet_tags`

---

## .state/query-branch

**Trigger:** Transition from `query-resolve` when results are ambiguous.
**Mode:** `match: all` — parallel faceted searches.
**Path:** Synchronous, shares tick budget with caller.

Runs two parallel queries to break ambiguity:

| Rule | Purpose |
|------|---------|
| `pivot1` | Facet-narrowed search using top tag facets |
| `bridge` | Cross-facet bridging search |

After both complete:
- If either has high margin → return done
- If budget remains → transition back to `query-resolve`
- Otherwise → return `stopped: ambiguous`

---

## .state/query-explore

**Trigger:** Transition from `query-resolve` as last resort.
**Mode:** `match: sequence`.
**Path:** Synchronous, shares tick budget with caller.

Wider exploratory search when resolve and branch haven't produced
high-confidence results.

1. Broad search with expanded limit
2. If high margin → return done
3. If budget remains → even wider search, then transition back to `query-resolve`
4. Otherwise → return `stopped: budget`

---

## .state/review-supernodes

**Trigger:** Daemon-enqueued review of one supernode candidate.
**Mode:** `match: sequence`.
**Path:** Background (`foreground: false`), so async actions run inline.

Reviews a single supernode (a high-cardinality entity like an email address,
URL, or file path with many inbound references). Synthesizes a factsheet
from the inbound evidence and writes it as a new version of the target item,
marking it `_supernode_reviewed`.

Steps:

1. `get` the target item (current content/summary)
2. `traverse` inbound references (evidence for the factsheet)
3. `generate` a new factsheet via LLM using a `.prompt/supernode/*` doc
4. `put` the factsheet as a new version, marking `_supernode_reviewed`

The new version triggers the normal `after-write` flow for summarization
and tagging. See `.meta/supernodes` for how reviewed supernodes get surfaced
back into context.

---

## Assessment

### .state/assess

**Trigger:** called as a subflow by `.state/put` and `.state/stub`.
**Mode:** `match: sequence` with a single default rule.
**Path:** Synchronous (completes before the caller writes).

Policy gate that runs before every write. The default returns all caller
params unchanged with `assessment: "ok"`. Override by adding fragments
under `.state/assess/*` (e.g., `.state/assess/virustotal`).

Returns a normalized directive that the caller (put or stub) uses for
the final write — including `stop_processing`, `skip_fetch`, rewritten
`content`/`summary`, and merged `tags`.

### .state/stub

**Trigger:** edge-tag processing, edge backfill, extracted link targets.
**Mode:** `match: sequence` — assess, then atomic insert-if-absent.
**Path:** Synchronous.

Creates a stub note only if it does not already exist. Calls
`.state/assess` first so assessment policy applies to all stub creation
paths uniformly. The stub ID itself is passed as `target_uri` since
stubs (unlike puts) don't have a separate URI field.

Will not overwrite existing notes — `changed: false` in the output means
the note already existed and was left untouched.

---

## Simple operation wrappers

These are thin state docs that wrap a single action, providing named flow
access to every store operation.

### .state/put

**Params:** `content`, `uri`, `id`, `tags`, `summary`, `queue_background_tasks`
**Actions:** `.state/assess` (subflow), then `put`.
**Output:** `{"id": "..."}`

Calls the assessment policy gate before writing. Assessment directives
can rewrite any field (e.g., replacing content with a malicious-URL
explanation). The `put` action receives the assessed values, not the
original params.

### .state/tag

**Params:** `id` (single item) or `items` (list from search results), `tags`
**Action:** `tag` — applies explicit tags to one or more items.
**Output:** `{"count": N, "ids": [...]}`

### .state/delete

**Params:** `id`
**Action:** `delete` — permanently removes an item.
**Output:** `{"deleted": "id"}`

### .state/move

**Params:** `name` (target ID), `source` (default: `"now"`), `tags` (filter), `only_current`
**Action:** `move` — extracts matching versions from source into target.
**Output:** `{"id": "...", "summary": "..."}`

The wrapper forwards `params.source` (not `params.source_id`) — see
`keep/data/system/state-move.md`. The Python API method `kp.move()` keeps
the historical `source_id=` keyword for the same field.

### .state/stats

**Params:** `top_k` (default: 10)
**Action:** `stats` — computes store profile for query planning.
**Output:** `{"total": N, "tags": {...}, "all_tags": [...], "dates": {...}, "structure": {...}}`

See [FLOW-ACTIONS.md](FLOW-ACTIONS.md) for detailed output shapes.

---

## Extending state docs

### Fragments

You can add processing steps to any state doc without editing the original.
Create a child note under the state doc's path:

```bash
# Add a custom step to after-write
keep put --id .state/after-write/obsidian-links 'rules:
  - when: "item.content_type == '\''text/markdown'\''"
    id: obsidian-links
    do: extract_links
    with:
      tag: references
      create_targets: "true"'
```

Child fragments are discovered automatically and merged into the base doc.
Each fragment has a `rules:` list (same syntax as a full state doc) and an
optional `order:` field.

The base doc and the fragments under its path are loaded together — for
example, `state-after-write.md` plus everything under `state-after-write/`.

### Ordering

The `order` field controls where fragment rules are inserted:

| Value | Effect |
|-------|--------|
| `after` (default) | Appended after all base rules |
| `before` | Prepended before all base rules |
| `after:{rule_id}` | Inserted after the named base rule |
| `before:{rule_id}` | Inserted before the named base rule |

For `match: all` pipelines (like `after-write`), order rarely matters — all
rules run in parallel. For `match: sequence` pipelines, order determines
execution position.

### Enabling and disabling

Fragments are active by default. To disable one without deleting it:

```bash
keep tag .state/after-write/obsidian-links -t active=false   # disable
keep tag .state/after-write/obsidian-links -r active         # re-enable
```

### Listing fragments

```bash
keep list .state/after-write/ --all
```

Shows all fragments with their tags, so active/inactive status is visible
at a glance.

---

## Editing state docs

State docs are regular keep notes. To edit one:

```bash
keep get .state/after-write          # View current content
keep edit .state/after-write         # Edit in $EDITOR
keep put ".state/after-write" ...    # Replace with new content
keep config --reset-system-docs      # Restore all defaults
```

Changes take effect on the next flow invocation. The built-in versions are
compiled into keep as a fallback — if a state doc is missing from the store,
the bundled version is used automatically.

## See also

- [FLOWS.md](FLOWS.md) — How flows work, with narrative and diagram
- [KEEP-FLOW.md](KEEP-FLOW.md) — Running, resuming, and steering flows
- [FLOW-ACTIONS.md](FLOW-ACTIONS.md) — Available actions reference
