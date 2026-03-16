---
tags:
  category: system
  context: state
---
match: sequence
rules:
  - id: profile
    do: stats
    with:
      top_k: "{params.top_k}"
  - return: done
