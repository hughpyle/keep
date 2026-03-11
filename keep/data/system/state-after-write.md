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
    when: "item.content_length > params.max_summary_length && !item.has_summary"
    do: summarize
  - id: described
    when: "item.has_uri && item.has_media_content && system.has_media_provider"
    do: describe
post:
  - return: done
