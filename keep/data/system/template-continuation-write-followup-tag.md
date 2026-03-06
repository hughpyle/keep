---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Write followup: provider-backed tagging"
---
{
  "followups": [
    {
      "task_type": "tag",
      "when": {
        "param_true": "processing.tag"
      },
      "content": "$write.content",
      "metadata": {
        "provider": "$params.processing.tag_provider",
        "provider_params": "$params.processing.tag_provider_params"
      }
    }
  ]
}
