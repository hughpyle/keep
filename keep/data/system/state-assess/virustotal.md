---
tags:
  category: system
  context: state-fragment
---
# Assess a note using VirusTotal lookup for URI or hash
order: before:default
rules:
  - id: virustotal
    do: assess_virustotal
    with:
      target_id: "{params.target_id}"
      target_uri: "{params.target_uri}"
      source: "{params.source}"
      id: "{params.id}"
      uri: "{params.uri}"
      content: "{params.content}"
      tags: "{params.tags}"
      summary: "{params.summary}"
      created_at: "{params.created_at}"
      force: "{params.force}"
      queue_background_tasks: "{params.queue_background_tasks}"
  - return:
      status: done
      with:
        assessment: "{virustotal.assessment}"
        id: "{virustotal.id}"
        uri: "{virustotal.uri}"
        content: "{virustotal.content}"
        tags: "{virustotal.tags}"
        summary: "{virustotal.summary}"
        created_at: "{virustotal.created_at}"
        force: "{virustotal.force}"
        queue_background_tasks: "{virustotal.queue_background_tasks}"
        stop_processing: "{virustotal.stop_processing}"
        skip_fetch: "{virustotal.skip_fetch}"
