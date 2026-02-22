---
tags:
  category: system
  context: meta
---
# .meta/genre — Same Genre

Items tagged with the same genre. Surfaces related media
when viewing an item that has a genre tag.

## Injection

Meta docs are query patterns, not prompts. Lines with `=*` (e.g., `genre=*`) match any item that has this tag set. Lines with trailing `=` scope results to the viewed item's value. Meta docs currently drive query-based surfacing, not LLM prompt construction.

genre=*
genre=
