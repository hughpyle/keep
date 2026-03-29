# Unit 3: Projections And System-Doc Roles

Date: 2026-03-28
Status: Draft

## Goal

Move semantic shaping into the flow runtime:

- rendering-related shaping
- filtering and context assembly
- prompt expansion inputs
- default behavior selection through user-editable system docs resolved by role

This unit combines the old PRs 6 and 7.

## Problem

Today, even where flows exist, important behavior is still split across:

- bespoke Python rendering and shaping logic
- hard-coded system-doc IDs
- bundled or inline Python fallbacks

That breaks the intended semantic layering. The system should instead have:

- generic runtime primitives
- role-resolved system docs that choose defaults and composition
- thin clients that render structured results

## Scope

### In scope

- projection primitives in the flow runtime
- role-based system-doc resolution
- removal of hard-coded system-doc identity assumptions in essential paths
- explicit failure for invalid user-edited system docs

### Out of scope

- daemon and queue hardening
- local integration cleanup except where current code blocks projection migration

## Primary Design

### 1. Projection primitives

Introduce generic projection primitives that can be applied during flow execution.

Examples:

- item projection
- context projection
- version projection
- part projection
- visible-tag filtering
- deep-group projection
- prompt context projection
- summary/display-oriented shaping

These primitives should be selected by:

- explicit flow params when the caller asks for a specific view
- system-doc defaults when the caller does not specify one

### 2. Role-based system-doc resolution

System docs should be resolved by semantic role rather than hard-coded note ID.

Examples of roles:

- `state.get`
- `state.find`
- `state.put`
- `projection.context.default`
- `projection.find.default`
- `prompt.reflect`
- `prompt.query`
- `analysis.incremental`
- `tag-schema.act`

The resolver should map roles to active documents in the store. Bundled docs are only for bootstrap or reset.

### 3. No silent semantic fallback

If a user-edited system doc is invalid:

- surface that failure explicitly
- do not silently fall back to Python-owned behavior

The runtime should remain generic, but active semantics must come from the editable doc layer once bootstrap is complete.

## File Plan

### New [keep/projections.py](/Users/hugh/play/keep/keep/projections.py)

- define generic projection primitives and helpers

### New [keep/system_doc_roles.py](/Users/hugh/play/keep/keep/system_doc_roles.py)

- define role lookup and active-document resolution

### [keep/state_doc.py](/Users/hugh/play/keep/keep/state_doc.py)

- integrate role-based loading
- stop treating missing/invalid store docs as a reason to silently resume Python-owned semantics

### [keep/_context_resolution.py](/Users/hugh/play/keep/keep/_context_resolution.py)

- move prompt/context shaping to projections plus role-resolved docs
- remove hard-coded prompt identity assumptions from essential paths

### [keep/analyzers.py](/Users/hugh/play/keep/keep/analyzers.py)

- resolve prompt/analysis behavior through roles
- reduce or remove direct Python prompt fallbacks after bootstrap

### [keep/system_docs.py](/Users/hugh/play/keep/keep/system_docs.py)

- keep bootstrap/reset responsibilities
- stop being the hidden runtime semantic source of truth

### [keep/thin_cli.py](/Users/hugh/play/keep/keep/thin_cli.py)

- keep rendering as formatting of structured returned data
- stop embedding semantic filtering/shaping decisions that belong to projections

## Sequencing

1. Add projection primitives with no user-facing behavior change.
2. Convert one high-value path, likely `get-context`, to projection-driven shaping.
3. Add role-based system-doc resolution.
4. Migrate prompts and analyzer prompt selection to role-based lookup.
5. Remove silent fallbacks in essential semantic paths.

## Acceptance Criteria

- Shaping and filtering of memory results happen in the flow runtime.
- Clients request projections; they do not own semantics.
- Essential behavior is configured by role-resolved editable system docs.
- Invalid system docs fail explicitly rather than silently restoring Python defaults.
- Essential behavior no longer depends on fixed system-doc IDs.

## Risks

### Too much configurability too early

Projection primitives should stay small and generic. The role/doc layer should compose them. Do not add one-off primitives for every legacy rendering branch.

### Half-migrated semantics

If prompts or context assembly remain partly hard-coded while other pieces move to role-based docs, the system will become harder to reason about than it is today. This unit should migrate complete semantic slices, not isolated helper functions.
