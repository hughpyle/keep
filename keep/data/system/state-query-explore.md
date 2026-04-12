---
tags:
  category: system
  context: state
---
# Wider exploratory search. Last resort when resolve/branch
# haven't produced high-confidence results.
match: sequence
rules:
  - when: "!(has(params.query) && params.query != '' && params.query != null)"
    # Query flows require a non-empty search string.
    return:
      status: error
      with:
        reason: "query required"
  - id: search
    # Broad exploratory search
    do: find
    with:
      query: "{params.query}"
      limit: "{params.explore_limit}"
  - when: "search.margin > params.margin_high"
    return: done
  - when: "budget.remaining > 0"
    # Still have budget — try even wider, then re-resolve
    do: find
    with:
      query: "{params.query}"
      limit: "{params.explore_limit_wide}"
    then: query-resolve
  - return:
      status: stopped
      with:
        reason: "budget"
