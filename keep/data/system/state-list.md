---
tags:
  category: system
  context: state
---
match: sequence
rules:
  - id: results
    do: find
    with:
      prefix: "{params.prefix}"
      tags: "{params.tags}"
      since: "{params.since}"
      until: "{params.until}"
      order_by: "{params.order_by}"
      include_hidden: "{params.include_hidden}"
      limit: "{params.limit}"
  - return: done
