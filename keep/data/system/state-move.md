---
tags:
  category: system
  context: state
---
# Rename a note or retag in bulk.
match: sequence
rules:
  - id: moved
    do: move
    with:
      name: "{params.name}"
      source: "{params.source}"
      tags: "{params.tags}"
      only_current: "{params.only_current}"
  - return: done
