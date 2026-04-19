---
tags:
  category: system
  context: meta
---
# .meta/learnings — Experiential Priming
#
# Past learnings, breakdowns, and gotchas. Before starting work,
# check what went wrong or was hard-won last time. The goal isn't
# caution — it's not re-learning things the hard way.
#
# Each rule finds items of a specific kind, ranked by semantic
# similarity to the item being viewed. If you're looking at an
# auth-related item, auth-related learnings surface first.
#
# Available params: see .meta/todo for the full list.
match: all
rules:
  - id: learnings
    do: find
    with:
      similar_to: "{params.item_id}"
      tags: {kind: learning}
      limit: "{params.limit}"
  - id: breakdowns
    do: find
    with:
      similar_to: "{params.item_id}"
      tags: {kind: breakdown}
      limit: "{params.limit}"
  - id: gotchas
    do: find
    with:
      similar_to: "{params.item_id}"
      tags: {kind: gotcha}
      limit: "{params.limit}"
