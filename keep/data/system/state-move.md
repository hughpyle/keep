---
tags:
  category: system
  context: state
---
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
