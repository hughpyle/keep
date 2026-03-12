---
tags:
  category: system
  context: state-fragment
---
rules:
  - id: resolve_stubs
    when: "item.has_uri && !item.is_system_note && item.tags._source != 'link'"
    do: resolve_stubs
