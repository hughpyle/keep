---
tags:
  category: system
  context: state-fragment
---
# Decompose long or URI-backed notes into searchable parts.
# Skips system notes, link stubs, and auto-vivified stubs.
rules:
  - id: analyzed
    when: "!item.id.startsWith('.') && (item.content_length > 500 || item.uri != '') && !(has(item.tags._source) && item.tags._source == 'link') && !(has(item.tags._source) && item.tags._source == 'auto-vivify')"
    do: analyze
