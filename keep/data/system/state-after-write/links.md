---
tags:
  category: system
  context: state-fragment
---
rules:
  - id: linked
    when: "!item.is_system_note && item.has_content && item.content_type == 'text/markdown'"
    do: extract_links
    with:
      tag: references
      create_targets: "true"
