---
tags:
  category: system
  context: state-fragment
---
# Auto-classify notes using constrained tag taxonomies.
rules:
  - id: tagged
    when: "!item.id.startsWith('.') && item.content_length > 0"
    do: auto_tag
