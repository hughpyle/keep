---
tags:
  category: system
  context: state-fragment
---
# Flag near-duplicate notes after write.
rules:
  - id: find-duplicates
    when: "!item.is_system_note && item.has_content"
    do: resolve_duplicates
    with:
      tag: duplicates
