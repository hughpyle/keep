---
tags:
  category: system
  context: state
---
# Context assembly for OpenClaw agent turns.
# Runs five parallel queries to surface relevant context.
# Edit this to customize what context the agent sees.
match: all
rules:
  - id: intentions
    do: get
    with:
      id: "now"

  - id: similar
    do: find
    with:
      query: "{params.prompt}"
      bias: { "now": 0 }
      limit: 7

  - id: meta
    do: resolve_meta
    with:
      item_id: "now"
      limit: 3

  - id: edges
    do: resolve_edges
    with:
      id: "now"
      limit: 5

  - id: session
    do: get
    with:
      id: "{params.item_id}"
post:
  - return: done
