---
tags:
  category: system
  context: tag-description
  _inverse: frames
  _value_regex: '^.+\?$'
---
# Tag: `frame` — Current Interpretive Stance

The `frame` tag points at a note that names the current practical view or
interpretive stance for the work at hand.

The intended sense is simple: a frame is a current way of seeing and acting in
the situation. It helps keep related cues, procedures, and prior cases attached
to the live hypothesis rather than collapsing them into a settled category.

If a note has `frame: debugging?`, then `debugging?` is the target note, and
`get debugging?` shows the source note under `frames:`.

## Characteristics

- **Edge-creating**: `_inverse: frames` makes this a navigable edge tag.
- **Pattern-constrained**: `_value_regex` requires the target note ID to end in
  `?`.
- **Ordinary-note targets**: frame targets are normal notes, not `.tag/*`
  children or other system docs.
- **Auto-vivifying**: if the target note does not exist yet, keep creates a
  stub note for it automatically.

## Usage

```bash
# Put a note into the current debugging frame
keep put "Investigate why the daemon is not restarting." \
  --id restart-debug \
  -t frame='debugging?'

# Add or change a frame later
keep tag restart-debug -t frame='repair?'

# See everything currently gathered under a frame
keep get 'debugging?'
```

## Why Keep `?` In The Note ID

The question mark is part of the target note ID itself, not just presentation.
That keeps `debugging` and `debugging?` distinct:

- `debugging` can remain a settled topic or reference note
- `debugging?` names a live framing hypothesis

In practice, `frame` also helps retrieval by narrowing which distinctions matter
for the current note. It acts as a lightweight variety filter for querying and
context assembly: relevant to the live frame, not to every note with a similar
topic label.
