# Edge Target Display Names

## Problem

Some edge targets are stable machine identifiers, but users need a human label.

Examples:

- Hermes contact IDs should be stable per platform and user:
  `contact:telegram:42`
- Email addresses are canonical targets:
  `alice@example.com`

Those IDs are appropriate edge targets, but they are not always the best thing
to show in UI or prompt context.

## Existing Syntax

Keep already supports labeled refs in tag values:

- `[[target_id|Label]]`

This is currently used for labeled references and parsed by `parse_ref()`.
It is safe to reuse for edge tags.

## Proposal

Use labeled refs for edge targets that have a canonical ID plus a human label.

Examples:

- `from: [[contact:telegram:42|Alice]]`
- `from: [[alice@example.com|Alice Example]]`

The edge target remains the canonical ID (`contact:telegram:42`,
`alice@example.com`).  The label is auxiliary metadata.

## Target Note Convention

Do not add a dedicated display-name field to items.

Instead, store observed labels on the target note as tags:

- `name`
- optionally `title` for document-like notes

Display logic should prefer:

1. `name`
2. `title`
3. summary

When `name` or `title` is multi-valued, display should use the last value.
Values preserve insertion order, so the most recently observed distinct label
wins for display.

## Edge Creation Behavior

When edge processing sees a labeled ref:

1. Parse the target ID and label with `parse_ref()`
2. Create or resolve the target note normally
3. If a non-empty label is present, merge it into the target note's `name` tag

This should be generic core behavior, not Hermes-specific.

Initial implementation only merges observed labels into auto-vivified targets.
This is deliberate: user-curated existing notes should not accumulate
machine-observed `name` values automatically.

So the supported case is:

- the target is newly auto-vivified, or still an auto-vivified stub

Existing non-auto-vivified targets keep their current `name` / `title` tags
unless a user or higher-level workflow updates them explicitly.

If multiple edge tags on the same source note point at the same target with
different labels in a single processing pass, labels should be deduplicated
before writing the target note.

## Versioning / Churn

Observed labels may vary over time.

Examples:

- `Alice`
- `Alice Smith`
- `alice_s`

We do not want a new content/version event for every observed variant.

Preferred behavior:

- treat `name` as a multi-valued set
- append new distinct values when discovered
- preserve first-seen order
- display the last value

Initial implementation can tolerate normal version creation for machine-added
`name` updates. Avoiding extra version churn is desirable, but should be treated
as a follow-up optimization unless a clean silent metadata-update path exists.

Possible future optimization paths:

- a silent tag-merge API for machine-owned metadata
- a write-path flag that suppresses version creation for enrichment-only tag updates

Without that optimization, a target note may accumulate version events as new
labels are observed. That is acceptable for an initial implementation.

## Label Removal

Observed labels are append-only.

If a later ref omits a label, or uses a different label, previously observed
`name` values should not be removed automatically. The absence of a label is not
evidence that an earlier observed name is false.

## Why This Is Clean

- Canonical IDs remain stable
- Human labels travel with the existing ref syntax
- Hermes contacts and email senders/recipients use the same mechanism
- Target enrichment happens in one core place
- Items remain structurally compact: `name` stays a tag, not a new field
