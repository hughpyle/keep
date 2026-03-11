---
tags:
  category: system
  context: state-fragment
---
rules:
  - id: analyzed
    when: "!item.is_system_note"
    do: analyze
