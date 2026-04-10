---
tags:
  category: system
  context: state
---
# Policy gate invoked by put and stub before writing.
# Default: pass through all caller params unchanged (no-op assessment).
# Override by adding fragments under .state/assess/* (e.g., virustotal).
match: sequence
rules:
  - id: default
    return:
      status: done
      with:
        assessment: "ok"
        id: "{params.id}"
        uri: "{params.uri}"
        content: "{params.content}"
        tags: "{params.tags}"
        summary: "{params.summary}"
        created_at: "{params.created_at}"
        force: "{params.force}"
        queue_background_tasks: "{params.queue_background_tasks}"
        stop_processing: false
        skip_fetch: false
