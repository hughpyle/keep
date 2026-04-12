---
tags:
  category: system
  context: state
---
# Parallel faceted search. Tries pivot and bridge queries, then
# returns to query-resolve if results are still ambiguous.
match: all
rules:
  - id: pivot1
    # Facet-narrowed search
    when: "has(params.query) && params.query != '' && params.query != null"
    do: find
    with:
      query: "{params.query}"
      limit: "{params.pivot_limit}"
  - id: bridge
    # Cross-facet bridging search
    when: "has(params.query) && params.query != '' && params.query != null"
    do: find
    with:
      query: "{params.query}"
      limit: "{params.bridge_limit}"
post:
  - when: "!(has(params.query) && params.query != '' && params.query != null)"
    return:
      status: error
      with:
        reason: "query required"
  - when: "pivot1.margin > params.margin_high || bridge.margin > params.margin_high"
    return: done
  - when: "budget.remaining > 0"
    then: query-resolve
  - return:
      status: stopped
      with:
        reason: "ambiguous"
