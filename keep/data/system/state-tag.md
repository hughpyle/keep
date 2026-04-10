---
tags:
  category: system
  context: state
---
# Add or remove tags on one or more notes.
match: sequence
rules:
  - id: tagged
    do: tag
    with:
      id: "{params.id}"
      items: "{params.items}"
      tags: "{params.tags}"
      remove: "{params.remove}"
      remove_values: "{params.remove_values}"
  - return: done
