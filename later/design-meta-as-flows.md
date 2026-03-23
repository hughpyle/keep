# Meta-Docs as Flows

## Problem

Meta-docs (`.meta/*`) currently use a bespoke line-based format parsed by `_parse_meta_doc()`:

```
act=commitment status=open     ← query (AND conditions)
project=                       ← context key (expand from current item)
genre=*                        ← prerequisite (item must have this tag)
```

Three regex patterns, three concepts (`query`, `context`, `prereq`), one custom parser. This format cannot express flow execution (needed for `.meta/ongoing/*` tasks like supernode review), and adding flow support would mean either a second format or a superset DSL.

## Design

**Meta-docs become state docs.** Same `match`/`rules` syntax, same `run_flow()` runtime, same action registry. No new keywords, no new parser.

### Current meta-docs rewritten

**`.meta/todo`** — open loops:
```yaml
match: all
rules:
  - id: commitments
    do: find
    with:
      tags: {act: commitment, status: open, project: "{params.project}"}
      limit: "{params.limit}"
  - id: requests
    do: find
    with:
      tags: {act: request, status: open, project: "{params.project}"}
      limit: "{params.limit}"
  - id: offers
    do: find
    with:
      tags: {act: offer, status: open, project: "{params.project}"}
      limit: "{params.limit}"
  - id: blocked
    do: find
    with:
      tags: {status: blocked, topic: "{params.topic}"}
      limit: "{params.limit}"
```

**`.meta/learnings`** — experiential priming:
```yaml
match: all
rules:
  - id: learnings
    do: find
    with:
      tags: {type: learning, project: "{params.project}"}
      limit: "{params.limit}"
  - id: breakdowns
    do: find
    with:
      tags: {type: breakdown, topic: "{params.topic}"}
      limit: "{params.limit}"
  - id: gotchas
    do: find
    with:
      tags: {type: gotcha, project: "{params.project}"}
      limit: "{params.limit}"
```

**`.meta/genre`** — same genre (with prereq as `when` guard):
```yaml
match: sequence
rules:
  - when: "!params.genre"
    return: done
  - id: same_genre
    do: find
    with:
      tags: {genre: "{params.genre}"}
      limit: "{params.limit}"
```

**`.meta/artist`**, **`.meta/album`** — same pattern as genre.

### New: flow-based meta-docs

Because meta-docs *are* state docs, they can use any action — including expensive ones that delegate to the work queue:

```yaml
# .meta/ongoing/supernode-review
match: sequence
rules:
  - id: discover
    do: find_supernodes
    with:
      min_fan_in: "{params.min_fan_in}"
      limit: "{params.limit}"
  - when: "discover.count == 0"
    return: done
  - id: description
    do: generate
    with:
      prompt: "supernode"
      id: "{discover.results[0].id}"
  - id: updated
    do: put
    with:
      id: "{discover.results[0].id}"
      content: "{description.text}"
      tags:
        _supernode_reviewed: "{now}"
  - return: done
```

No special syntax. The `generate` step is expensive, so the flow runtime delegates it (and everything after) to the work queue. The inline portion returns `discover` results immediately.

## How meta resolution changes

### Current flow

1. `resolve_meta()` loads all `.meta/*` docs
2. `_parse_meta_doc(rec.summary)` extracts query lines, context keys, prereqs
3. `_resolve_meta_queries()` expands queries, runs `list_items()`, ranks results
4. Returns `{meta_name: [Item, ...]}`

### New flow

1. `resolve_meta()` loads all `.meta/*` docs
2. Each doc is a state doc — parse it with `parse_state_doc()`
3. Run it with `run_flow()`, passing current item's tags as `params`
4. Collect results from action bindings
5. Returns `{meta_name: results}`

The custom parser (`_parse_meta_doc`), the query expansion logic (`_resolve_meta_queries`), the prereq/context-key concepts — all replaced by the existing state-doc runtime.

### Params passed to meta flows

Meta resolution provides the current item's context as `params`:

```python
params = {
    "item_id": item_id,
    "limit": limit_per_doc,
    # Current item's tags, flattened into params:
    "project": current_tags.get("project", ""),
    "topic": current_tags.get("topic", ""),
    "genre": current_tags.get("genre", ""),
    "artist": current_tags.get("artist", ""),
    "album": current_tags.get("album", ""),
    # ... all non-underscore tags
}
```

When a param is empty string and used in `tags: {project: "{params.project}"}`, the `find` action should treat empty-string tag values as "no filter" (skip that key). This makes context expansion implicit — if the current item has no `project` tag, the filter is simply absent.

## What goes away

- `_parse_meta_doc()` — replaced by `parse_state_doc()`
- `_resolve_meta_queries()` — replaced by `run_flow()`
- `resolve_inline_meta()` — replaced by `run_flow()` with ad-hoc state doc
- `_META_QUERY_PAIR`, `_META_CONTEXT_KEY`, `_META_PREREQ_KEY` regex patterns
- The concepts of "context key" and "prerequisite" as distinct from flow logic

## What stays the same

- `.meta/*` docs are still system docs with `context: meta` tag
- `resolve_meta()` public API signature unchanged
- `ResolveMeta` action unchanged (calls `resolve_meta()`)
- `get-context` state doc unchanged (calls `resolve_meta` action)
- Part-to-parent uplift and relevance ranking — move into a post-processing step or a dedicated action

## Migration

1. Ship new `.meta/*` system docs in state-doc format (SYSTEM_DOCS_VERSION bump)
2. `_parse_meta_doc()` tries `parse_state_doc()` first; if it fails (no `match`/`rules`), falls back to legacy line parser
3. Legacy parser emits a deprecation warning in debug log
4. Remove legacy parser after one version cycle

## Open questions

- **Ranking**: Current meta resolution applies embedding similarity + recency decay across all results. With flow-based resolution, each action returns its own results. Where does cross-result ranking happen — in a `post` block, in the `resolve_meta()` wrapper, or in a dedicated ranking action?
- **Empty params**: Should `find` with `tags: {project: ""}` mean "no filter on project" or "match items where project is empty string"? The former is more useful for meta-docs, but might surprise in other contexts. Could use a sentinel like `tags: {project: "{params.project|skip_empty}"}` but that's new template syntax.
- **Budget**: Meta resolution runs during `get`/`now` — how much budget should meta flows get? Currently `get-context` uses `budget=5`. Each meta-doc flow consumes from that same budget, or gets its own?
