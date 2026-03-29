# Unit 3: Deep Token-Budget Review

Date: 2026-03-29
Status: Working Notes

## Purpose

Capture the current `deep` token-budget strategy before Unit 3 starts
extracting projection/filter semantics into reusable runtime primitives.

This is not a commitment to preserve every current heuristic forever.
It is an inventory of the behavior that exists today, the seams where
it crosses retrieval/projection/filtering concerns, and the functional
decisions that must be made before stabilizing new projection roles.

## Current Code Paths

Primary behavior today is split across:

- [render_find_context](../../keep/cli.py)
- [expand_prompt](../../keep/cli.py)
- [Keeper._find_direct](../../keep/api.py)
- [SearchAugmentationMixin._deep_edge_follow](../../keep/_search_augmentation.py)
- [SearchAugmentationMixin._deep_tag_follow](../../keep/_search_augmentation.py)
- [builtin `find-deep` state doc](../../keep/builtin_state_docs.py)

The important observation is that `deep` is not only a retrieval mode.
It is also a result-shaping mode and a budget allocation policy.

## Current Deep Strategy

### 1. Retrieval overfetch and expansion

`deep=True` changes retrieval before any rendering happens:

- hybrid search increases semantic overfetch
- `similar_to` search increases the retrieval window
- deep search may force system-doc migration and edge-backfill
- deep search uses one of two expansion paths:
  - edge-follow if edges exist
  - flow/tag-follow fallback if edges do not exist

This means `deep` currently bundles:

- a primary retrieval policy
- an expansion policy
- a store-initialization policy

That is already wider than a pure projection concept.

### 2. Result shape

The retrieval layer currently returns:

- `results`: ranked primary items
- `deep_groups`: grouped related items keyed by a primary or injected entity

The renderer assumes:

- deep groups are optional
- deep groups are associated with a visible primary anchor
- some primaries may be synthetic entities promoted for rendering

The grouped shape is already useful as a reusable projection model.
The rules for how anchors enter `results` are not yet obviously stable.

### 3. Token-budget rendering passes

Current rendering in [render_find_context](../../keep/cli.py) is a
three-pass budget allocator:

1. Pass 1: render summary lines for top-level primaries.
2. Pass 2: render deep evidence bundles from remaining budget.
3. Pass 3: backfill parts and versions from whatever budget remains.

Important detail: in code comments, “Pass 2” is deep evidence and
“Pass 3” is detail backfill, but the function docstring still describes
the older order. The implementation, not the docstring, is authoritative.

### 4. Pre-budget primary capping

When `deep_primary_cap` is set and deep groups exist:

- primaries are sorted to prefer `_entity` items first
- then items with deep groups
- then original rank order
- only the top `deep_primary_cap` primaries remain

This happens before any budget is spent.

This is a cross-cutting policy:

- partly retrieval semantics, because it changes which top-level items survive
- partly projection semantics, because it determines visible anchors
- partly budgeting semantics, because it protects budget for deep evidence

### 5. Deep bundle allocation policy

Deep-group rendering is not simple per-group truncation.

Current strategy:

- build bundles keyed by `(rendered block, parent thread)`
- dedup anchors within a bundle
- score each bundle by the best deep item score
- order bundles in two phases:
  - coverage first: one bundle per rendered block
  - density second: remaining bundles by score
- emit at most 1 anchor per bundle when `token_budget <= 900`, else 2

This is an explicit multi-section budget allocator, not just `top_k`.

### 6. Adaptive detail windows

For deep items with focused versions/parts, the renderer can expand a
local “Thread” or “Story” window around the evidence.

Current heuristics:

- compact mode when deep groups exist and `token_budget <= 300`
- thread radius:
  - `0` when budget hint `<= 450`
  - `1` when budget hint `<= 900`
  - `2` otherwise
- skip empty headers
- skip degenerate thread windows that merely restate the already-rendered focus

This is an important model for future projection filtering:
the budget is spent on richer context windows only when enough budget remains.

### 7. Token estimation

Current token estimation is intentionally crude:

- `tokens ~= len(text) // 4`

This is cheap and deterministic, which is useful for runtime filtering.
It is also approximate enough that any stable contract should describe it
as estimation, not exact token accounting.

## Cross-Cutting Behaviors To Preserve Or Reconsider

These are the behaviors currently embedded in the “deep” model:

- protect budget for deep evidence before parts/versions
- keep anchor coverage before maximizing dense evidence from a single thread
- adapt thread/story expansion to remaining budget
- avoid emitting orphan headers
- dedup anchors within and across bundles
- prefer query-mentioned entities when choosing capped primaries

These look like generally useful projection/filter ideas.

These look more like compatibility heuristics than stable semantics:

- exact thresholds: `300`, `450`, `900`
- exact anchor-per-bundle counts
- exact entity-first ordering rule
- exact primary-cap suppression behavior
- exact `len(text) // 4` estimate

## Functional Decisions To Prioritize

### Must decide before stabilizing new projection roles

1. Is token budget a general filter operator over structured results, or only a renderer concern?
2. Does budget trimming happen after retrieval only, or may it influence retrieval overfetch hints?
3. Is grouped related evidence a first-class projection shape for all searches, or only for `deep`-like expansions?
4. Are entity promotion and anchor capping projection policy, or retrieval semantics?
5. Should the runtime expose explicit section priorities instead of hard-coded pass ordering?

### Should likely become configurable primitives

1. Select top-level projection sections:
   - `results`
   - `groups`
   - `parts`
   - `versions`
   - `similar`
   - `meta`
2. Apply count-based filters:
   - `top_k`
   - `per_group_k`
   - `anchors_per_bundle`
3. Apply budget-based filters:
   - `token_budget`
   - optional section priorities
   - optional compact/detail modes
4. Choose grouping mode:
   - `flat`
   - `grouped`
   - `grouped_with_windows`

### Should remain implementation details unless proven otherwise

1. Exact FTS/embedding fetch multipliers
2. Exact thread radius cutoffs
3. Exact stopword/tokenization implementation
4. Exact local store optimization path for edge-follow reranking

## Provisional Design Direction

The most promising direction for Unit 3 is:

1. Separate retrieval from projection.
2. Treat token budget as a general filter operator over projected sections.
3. Represent output as structured sections first, render text second.
4. Preserve current deep behavior as a compatibility preset while extracting generic operators.

That implies a future shape more like:

- retrieval plan
- expansion plan
- projection sections
- filter plan
- rendering plan

Rather than a single boolean `deep`.

## Immediate Unit 3 Guidance

During the initial Unit 3 pass:

- preserve current user-visible `deep` behavior
- do not create a new stable public flow name for the current deep bundle
- extract reusable budget/filter concepts from the renderer
- keep a running decision log when a current heuristic looks too specific to freeze

## Open Questions

1. Should `token_budget` filtering be stable and deterministic across transports?
2. Should grouped projections expose truncation diagnostics such as:
   - dropped sections
   - dropped anchors
   - estimated tokens consumed
3. Should parts/versions windows be projections in their own right, or only enrichments attached to other projected results?
4. Should “coverage first, density second” become a named bundle-selection strategy?
5. How much of the current deep renderer belongs in system-doc-configured defaults versus engine-owned projection operators?
