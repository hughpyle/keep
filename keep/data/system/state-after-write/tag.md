---
tags:
  category: system
  context: state-fragment
---
# Auto-classify notes using constrained tag taxonomies.
rules:
  - id: tagged
    when: "!item.is_system_note && item.has_content"
    do: auto_tag
