---
tags:
  category: system
  context: state
---
# Compute store profile statistics for query planning.
match: sequence
rules:
  - id: profile
    do: stats
    with:
      top_k: "{params.top_k}"
  - return: done
