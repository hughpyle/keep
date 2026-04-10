---
tags:
  category: system
  context: state
---
# Delete a note and optionally its version history.
match: sequence
rules:
  - id: result
    do: delete
    with:
      id: "{params.id}"
      delete_versions: "{params.delete_versions}"
  - return: done
