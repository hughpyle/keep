---
tags:
  category: system
  context: state
---
# List version history for a note.
match: sequence
rules:
  - id: versions
    do: list_versions
    with:
      id: "{params.id}"
      item_id: "{params.item_id}"
      limit: "{params.limit}"
  - return:
      status: done
      with:
        versions: "{versions}"
