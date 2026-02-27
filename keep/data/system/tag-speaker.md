---
tags:
  category: system
  context: tag-description
  _inverse: said
---
# Tag: `speaker` — Who Said It

The `speaker` tag identifies a person who said or wrote the content. When a document part is attributed to a specific speaker, tag it with `speaker: name`.

The `_inverse` declaration means this tag creates a navigable relationship edge. If `conv1@P{5}` has `speaker: Deborah`, then `get Deborah` will show it under `said:`.

## Characteristics

- **Edge-creating**: The `_inverse: said` declaration makes this an edge tag. Tagged documents become navigable links.
- **Auto-vivifying**: If the target person doesn't exist as a document, it's created automatically.
- **Unconstrained**: Values are free-form — any name is valid.
- **Part-level**: Most useful on conversation parts (`@P{N}`), where each part has a single speaker.

## Usage

```bash
# Tag a conversation part with a speaker
keep put "I think we should refactor the auth module" -t speaker=Deborah

# Find everything a speaker said
keep get Deborah
# → said:
# →   - conv1@P{5}  [2025-03-15] I think we should refactor...
# →   - conv2@P{3}  [2025-03-18] The API needs rate limiting...
```

## Naming

The tag value becomes the target document ID, so `speaker: Deborah` links to the document `Deborah`. IDs are case-sensitive — `Deborah` and `deborah` are different documents. Be consistent with casing. If the target document doesn't exist, it will be auto-created.

## Prompt

Identify who is speaking or who authored this content. Use a consistent name for each person. Only tag when attribution is clear and meaningful.
