---
tags:
  category: system
  context: state
---
# Runs after each put(). All matching rules fire in parallel.
# Additional rules are loaded from .state/after-write/* fragments.
match: all
rules:
  - id: summary
    when: "item.content_length > params.max_summary_length && item.summary == ''"
    do: summarize
    with:
      item_id: "{params.item_id}"
  - id: described
    when: "(item.uri.startsWith('file://') || item.uri.startsWith('/')) && (item.content_type.startsWith('image/') || item.content_type.startsWith('audio/') || item.content_type.startsWith('video/')) && system.has_media_provider"
    do: describe
    with:
      item_id: "{params.item_id}"
post:
  - return: done
