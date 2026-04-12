---
tags:
  category: system
  context: state
---
# Simple scoped search for memory_search tool.
match: all
rules:
  - id: results
    when: "has(params.query) && params.query != '' && params.query != null"
    do: find
    with:
      query: "{params.query}"
      scope: "{params.scope}"
      limit: "{params.limit}"
post:
  - when: "!(has(params.query) && params.query != '' && params.query != null)"
    return:
      status: error
      with:
        reason: "query required"
  - return: done
