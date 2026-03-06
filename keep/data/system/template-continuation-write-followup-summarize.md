---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Write followup: summarize long content through continuation task queue"
---
{
  "followups": [
    {
      "task_type": "summarize",
      "when": {
        "param_true": "processing.summarize"
      },
      "content": "$write.content",
      "tags": "$params.tags"
    }
  ]
}
