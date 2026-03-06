---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Write followup: analysis task scheduling"
---
{
  "followups": [
    {
      "task_type": "analyze",
      "when": {
        "param_true": "processing.analyze"
      },
      "content": "",
      "metadata": {
        "tags": "$params.processing.analyze_tags",
        "force": "$params.processing.analyze_force"
      }
    }
  ]
}
