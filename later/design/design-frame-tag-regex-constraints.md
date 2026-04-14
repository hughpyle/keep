# Regex-Constrained `frame` Edge Tag

Date: 2026-04-13
Status: Implemented

## Problem

We want a user-facing edge tag for the current practical view or interpretive
stance:

- `frame: debugging?`
- `frame: exploratory-design?`
- `frame: repair?`

This should point at ordinary notes whose IDs themselves end in `?`. Those
notes are not cosmetic labels; they are first-class frame notes that can gather
procedures, cues, prior history, and other material associated with that live
framing hypothesis.

The intended sense of `frame` is modest but specific: a current way of seeing
and acting in the situation. This is adjacent to Burbea's "view" and Flores'
"ontological stance" or "style", but the design does not depend on adopting any
special vocabulary beyond `frame`.

The inverse should read naturally:

- `.tag/frame` has `_inverse: frames`
- the target note `debugging?` then shows `frames: ...`

The existing tagdoc machinery does not support this pattern cleanly.

## Why Existing `_constrained` Is The Wrong Tool

Today `_constrained: true` has one specific meaning:

- the tag value must have a corresponding child note at `.tag/KEY/VALUE`

That behavior is documented and implemented as an enumerated taxonomy.

Current code paths depend on that exact meaning:

- `docs/TAGGING.md` documents `_constrained: true` as sub-note validation
- `keep/api.py::_validate_constrained_tags()` validates by checking for
  `.tag/KEY/VALUE`
- `keep/validate.py` warns if `_constrained` is anything other than `"true"`
  or absent
- `keep/analyzers.py::TagClassifier.load_specs()` only loads parent tagdocs with
  `_constrained == "true"` and then builds a classifier taxonomy from their
  children

So overloading `_constrained` to mean "regex-constrained" would be a semantic
break and would entangle value validation with the classifier taxonomy loader.

## Design Goal

Add a new tagdoc field for open-ended but pattern-constrained tag values,
without changing the existing meaning of `_constrained`.

For the motivating case:

- `frame` is an edge tag
- its values must end in `?`
- the actual target note ID contains the `?`
- the inverse is `frames`
- the frame note remains an ordinary note, not a hidden system note or enum
  child under `.tag/frame/*`

Functionally, a frame should also help retrieval. When present, it narrows which
procedures, cues, and prior cases are relevant to the current note. In that
practical sense, it acts as a variety filter for querying: not a taxonomy
label, but a constraint on which distinctions should matter right now.

## Proposal

Introduce a new parent-tagdoc field:

- `_value_regex`

Example:

```yaml
---
tags:
  _inverse: frames
  _value_regex: '^.+\?$'
---
# Tag: `frame`

A provisional framing hypothesis for the current event or activity stream.

Value must be the ID of a note ending in `?`.

Examples:
- `frame: debugging?`
- `frame: exploratory-design?`
- `frame: repair?`
```

## Semantics

### `_constrained`

Keep current behavior unchanged:

- `_constrained: true` means the value must exist as `.tag/KEY/VALUE`
- classifier taxonomy loading continues to use only `_constrained: true`

### `_value_regex`

New meaning:

- values are open-ended
- each value must match the supplied regular expression
- no `.tag/KEY/VALUE` child note is required
- this validation is write-time validation, not taxonomy/classifier loading

This gives three distinct tag modes:

1. open-ended
   - no `_constrained`, no `_value_regex`
2. enumerated constrained
   - `_constrained: true`
3. pattern-constrained
   - `_value_regex: '...'`

These modes should remain conceptually separate.

`_constrained: true` and `_value_regex` must not both be present on the same
parent tagdoc. If both appear, that is a tagdoc validation error.

## `frame` Tag Convention

The intended user-facing pattern is:

- source note: `frame: debugging?`
- target frame note ID: `debugging?`
- target frame note inverse view: `frames: [[source-id|...]]`

Important: the question mark is part of the target note ID itself, not merely a
label.

This preserves the epistemic stance directly in storage:

- `debugging` and `debugging?` are different notes
- `debugging?` names a live framing hypothesis, not a settled category

The same distinction matters operationally during query and context assembly:
`debugging?` should surface material relevant to the live debugging frame,
without collapsing it into the settled note `debugging`.

## Edge Tags And Validation Target

For edge tags, validation must apply to the canonical target ID, not the raw
surface string.

Examples that should be treated equivalently for regex validation:

- `frame: debugging?`
- `frame: [[debugging?|debugging?]]`

In both cases, the value being validated is:

- `debugging?`

This matters because edge-tag values may appear in normalized labeled-ref form.
The regex should not be evaluated against the literal `[[...]]` wrapper.

The target must also remain a valid ordinary note target for an edge:

- dot-prefixed system IDs are not valid `frame` targets
- `_value_regex` does not override normal edge-target validity rules

## Validation Order

The write path should behave as follows:

1. Load the parent tagdoc `.tag/{key}`
2. If the tagdoc has `_constrained: true`, apply existing enumerated-value
   validation exactly as today, including existing `_requires` behavior
3. If the tagdoc has `_value_regex` and `_requires`, apply regex validation only
   when the required tag is present, matching `_constrained` gating semantics
4. For edge tags (tagdocs with `_inverse`), parse/normalize the value using the
   same canonical target-ID logic as edge processing, then apply `_value_regex`
   to that target ID
5. For non-edge tags, apply `_value_regex` to the normalized stored value
6. Apply normal edge-target validity checks in addition to `_value_regex`

## Tagdoc Validation

Tagdoc validation should gain explicit handling for `_value_regex` on parent
`.tag/KEY` documents.

Expected rules:

- `_value_regex` must be a non-empty string
- the regex must compile successfully
- invalid regex syntax is a tagdoc validation error
- `_constrained: true` and `_value_regex` must not both be present
- `_value_regex` on child value docs `.tag/KEY/VALUE` is ignored or warned,
  since it only makes sense on the parent tagdoc

This should sit alongside existing validation of:

- `_inverse`
- `_constrained`
- `_singular`

But should not alter existing behavior for those fields.

## Classifier Behavior

`TagClassifier.load_specs()` should continue to treat only `_constrained: true`
as classifier taxonomy input.

Pattern-constrained tags should not automatically become classifier taxonomies,
because they do not have an enumerated child-value set.

This is important for keeping the analyzer boundary clean:

- enumerated constrained tags: classification taxonomy
- pattern-constrained tags: write-time validation only

## Documentation Changes

Update the user docs to describe four related capabilities:

1. plain tags
2. singular tags
3. enumerated constrained tags
4. pattern-constrained tags

Specific docs to update:

- `docs/TAGGING.md`
  - add a new subsection after constrained values describing `_value_regex`
  - explain that `_constrained` remains enumerated-child validation
  - include the `frame` example
- `docs/EDGE-TAGS.md`
  - add `frame` / `frames` as an example edge tag using `_value_regex`
  - clarify that edge-tag regex validation applies to the target ID

## Example System Docs

Parent tagdoc:

```yaml
---
tags:
  _inverse: frames
  _value_regex: '^.+\?$'
---
# Tag: `frame`

Points to a provisional frame note whose ID ends in `?`.
```

Example content note:

```yaml
---
tags:
  frame: debugging?
---
Investigate why the daemon is not restarting.
```

Example frame note:

```markdown
# debugging?

Signals, procedures, and prior cases associated with the current debugging
hypothesis.
```

## Testing

Add end-to-end tests covering:

### Parent tagdoc validation

- valid `_value_regex` accepted
- invalid regex rejected
- empty `_value_regex` rejected

### Write-time value validation

- `frame=debugging?` succeeds
- `frame=debugging` fails with a helpful error
- multi-value writes validate each value independently

### Edge-value forms

- raw value `debugging?` passes
- labeled ref `[[debugging?|debugging?]]` passes
- labeled ref to `[[debugging|debugging?]]` fails because target ID does not end
  in `?`

### Interaction with `_inverse`

- `frame` creates inverse `frames`
- inverse edge rendering still works when `_value_regex` is present

### Interaction with `_constrained`

- `_constrained: true` behavior is unchanged
- `_value_regex` alone does not require `.tag/KEY/VALUE` children
- classifier loading still ignores regex-only tagdocs
- `_constrained: true` and `_value_regex` together are rejected at tagdoc
  validation time

### Interaction with `_requires`

- `_value_regex` follows the same `_requires` gating semantics as `_constrained`
- when the required tag is absent, regex validation is skipped

## Error Message Shape

For failed regex validation, raise a direct error that names the tag, the bad
value, and the required pattern.

Example:

```text
Invalid value for tag 'frame': 'debugging'. Value must match regex '^.+\?$'
```

If the implementation wants more user-friendly wording for known tagdocs later,
that can be layered on top. The initial behavior should remain generic.

## Non-Goals

This design does not propose:

- changing the meaning of `_constrained`
- making `_constrained` and `_value_regex` composable on one tagdoc
- making regex-constrained tags participate in classifier taxonomies
- introducing a new special storage type for frame notes
- requiring frame notes to live under a namespace like `frame/debugging?`
- removing support for direct unlabeled edge values

The target note remains an ordinary note whose ID may itself contain `?`.

## Why This Is Clean

- keeps existing `_constrained` semantics stable
- separates enumerated taxonomies from pattern validation
- supports the desired `frame: debugging?` UX directly
- preserves `?` in the actual note ID, where the epistemic stance belongs
- reuses ordinary notes and ordinary edges rather than adding special-purpose
  storage machinery

## Open Question

Should `frame` remain a general user-defined edge tag, or should future query
planning treat it as a specially recognized retrieval hint?

Recommendation for initial implementation:

- keep `frame` fully generic at the storage layer
- let query and prompt assembly treat it as an ordinary edge tag initially

Reason:

- the storage and validation model should stay simple
- the note-level semantics are already useful without planner-specific behavior
- retrieval-specific optimizations can be added later if experience justifies
  them
