---
tags:
  category: system
  context: state
---
# Store a stub (auto-vivified from a reference edge).
match: sequence
rules:
  - id: assessed
    do: .state/assess
    with:
      target_id: "{params.id}"
      # Stubs do not have a separate uri field; when the target is a URL, the
      # stub ID itself is the assessable URI.
      target_uri: "{params.id}"
      source: "stub"
      id: "{params.id}"
      content: "{params.content}"
      tags: "{params.tags}"
      summary: "{params.summary}"
      created_at: "{params.created_at}"
      queue_background_tasks: "{params.queue_background_tasks}"
  - id: stored
    do: stub
    with:
      id: "{assessed.id}"
      content: "{assessed.content}"
      tags: "{assessed.tags}"
      summary: "{assessed.summary}"
      created_at: "{assessed.created_at}"
      queue_background_tasks: "{assessed.queue_background_tasks}"
  - return: done
