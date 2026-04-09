# Markdown Sync Content-Role Policy

Date: 2026-04-08
Status: Draft

This is a companion to
[markdown-sync-design.md](/Users/hugh/play/keep/later/design/markdown-sync-design.md).

Its purpose is to make the content-role refactor concrete before implementation
starts.

## Problem

Keep does not have one explicit policy for what kind of text is being written to
`documents.summary`.

Today the stored note body can be changed by several paths with different
semantics:

- initial note writes
- URI/file ingest
- summarize
- describe
- OCR result application

Those paths all ultimately write to the same stored body field, but they do not
all mean the same thing.

Markdown sync requires a clear answer to:

- when is the body authoritative authored note content
- when is the body a derived summary
- which write paths may replace it

## Chosen Model

Use a hybrid policy:

- per-note body authority marker
- per-write write-intent classification
- one central body-write decision function

This is more explicit than a pure path check and less invasive than trying to
derive everything from mirror registrations at every mutation site.

## 1. Per-note authority

Each current note has a system-level body authority state.

Initial values:

- `derived` = default for ordinary keep notes
- `markdown` = note body is authoritative synced markdown content

Suggested representation:

- system tag such as `_body_authority`

This is note state, not mirror state.

The mirror registry decides when a note should be imported/exported. The note's
body authority decides what kinds of body mutations are allowed.

## 2. Write intents

Every code path that attempts to change a note body must declare a write intent.

Initial intent set:

- `authoritative_input`
  - user-authored or source-authored note body input
- `derived_summary_replace`
  - replace body with a derived summary
- `derived_description_append`
  - append description/enrichment text to the stored body

If needed later, additional intents can be added, but v1 should not begin with
a large taxonomy.

## 3. Central policy function

All body writes must flow through one helper that takes:

- target note id
- existing note state
- body authority
- write intent
- proposed text
- optional content/hash metadata

That helper decides:

- allow and write
- allow but transform using existing derived-note rules
- reject body mutation and return a no-op

The helper is the only place that should answer whether a body write is valid.

## 4. Policy matrix

### Notes with `_body_authority=derived`

`authoritative_input`

- use existing keep behavior
- ordinary non-system note writes preserve current invariants:
  - short text may be stored verbatim
  - long text may store a placeholder/truncated body and queue summarize
- system-note behavior remains unchanged

`derived_summary_replace`

- allowed
- replace the stored body with the derived summary

`derived_description_append`

- allowed
- append/merge derived description text using current describe semantics

### Notes with `_body_authority=markdown`

`authoritative_input`

- allowed
- write the markdown body exactly as the authoritative current note body
- do not truncate
- do not replace it with a derived summary placeholder

`derived_summary_replace`

- rejected as a body mutation
- body stays unchanged
- summarize should be skipped for these notes

`derived_description_append`

- rejected as a body mutation
- body stays unchanged
- describe should be skipped for these notes

This means synced markdown notes are still analyzable, taggable, and linkable,
but their body is not overwritten by derived-text actions.

## 5. Current write inventory

These are the body-writing paths that matter now.

### A. Initial note write: `put()` / `_put_direct()` / `__upsert_impl()`

Files:

- [keep/api.py](/Users/hugh/play/keep/keep/api.py)

Current behavior:

- validates/normalizes input
- computes one `final_summary`
- for ordinary long notes, stores truncated placeholder body and later queues
  summarization

Required classification:

- ordinary `put` and URI ingest use `authoritative_input`
- the central policy decides whether that authoritative input becomes a derived
  body or a markdown-authoritative body

### B. Markdown sync import

New path to be added.

Required classification:

- `authoritative_input`
- also sets `_body_authority=markdown`

### C. Summarize action

Files:

- [keep/actions/summarize.py](/Users/hugh/play/keep/keep/actions/summarize.py)
- [keep/task_workflows.py](/Users/hugh/play/keep/keep/task_workflows.py)
- [keep/_background_processing.py](/Users/hugh/play/keep/keep/_background_processing.py)

Current behavior:

- computes a summary from note text
- emits `set_summary`
- stored body is replaced with that summary

Required classification:

- `derived_summary_replace`

Required markdown behavior:

- do not replace the body of markdown-authored notes
- summarize may still be skipped cleanly or record bookkeeping tags, but it may
  not change the body

### D. Describe action

Files:

- [keep/actions/describe.py](/Users/hugh/play/keep/keep/actions/describe.py)
- [keep/task_workflows.py](/Users/hugh/play/keep/keep/task_workflows.py)
- [keep/_background_processing.py](/Users/hugh/play/keep/keep/_background_processing.py)

Current behavior:

- appends a generated description to the existing stored body

Required classification:

- `derived_description_append`

Required markdown behavior:

- do not append to markdown-authored note bodies

### E. OCR result application

Files:

- [keep/actions/ocr.py](/Users/hugh/play/keep/keep/actions/ocr.py)
- [keep/task_workflows.py](/Users/hugh/play/keep/keep/task_workflows.py)
- [keep/_background_processing.py](/Users/hugh/play/keep/keep/_background_processing.py)

Current behavior:

- computes extracted text plus a summary
- writes the summary into the note body

Required classification:

- `derived_summary_replace`

Required markdown behavior:

- markdown-authored note bodies are not an OCR target in the normal sync model
- if the path is reached anyway, OCR must not overwrite the body

## 6. After-write flow consequences

The body-write helper is not sufficient on its own. The after-write flow should
also stop asking summarization/description paths to mutate markdown-authored
notes.

So v1 should gate:

- summarize
- describe

on body authority.

Other actions may continue to run against markdown-authored notes:

- analyze
- auto-tag
- extract_links

Those actions read authoritative body text but do not replace it.

## 7. Why not a pure mirror-registry check

A pure "is this note under an active mirror root" check at every mutation site
is not enough.

Problems:

- it couples note body semantics to external mirror configuration
- it makes ordinary body writes depend on path membership rather than note state
- it does not help with notes that remain markdown-authored after import but are
  not being actively mirrored at that exact moment

The note needs to carry its own body-authority semantics.

## 8. Why not write-intent alone

Write intent alone is also not enough.

It tells us what a mutation is trying to do, but not whether the target note is
meant to accept that kind of body mutation.

The policy needs both:

- target body authority
- mutation write intent

## 9. Implementation checkpoint

Before markdown sync implementation starts, the codebase should have:

- a named body-authority representation
- a named write-intent representation
- one central body-write helper used by all body-changing paths listed above

Until that exists, the markdown sync plan remains under-specified at its most
important seam.
