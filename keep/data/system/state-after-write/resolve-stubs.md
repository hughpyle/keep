---
tags:
  category: system
  context: state-fragment
---
# Fetch content for URI-backed stubs that don't have content yet.
# Skips link-sourced stubs (those are just reference placeholders).
rules:
  - id: resolve_stubs
    when: "item.has_uri && !item.is_system_note && !(has(item.tags._source) && item.tags._source == 'link')"
    do: resolve_stubs
