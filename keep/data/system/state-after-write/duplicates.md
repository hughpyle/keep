---
tags:
  category: system
  context: state-fragment
---
# Flag near-duplicate notes after write.
rules:
  - id: find-duplicates
    when: "!item.id.startsWith('.') && item.content_length > 0"
    do: resolve_duplicates
    with:
      tag: duplicates
