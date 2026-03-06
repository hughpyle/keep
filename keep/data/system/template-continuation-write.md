---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Main write continuation template composed from reusable write fragments"
---
{
  "include": [
    "write-core",
    "write-followup-summarize",
    "write-followup-ocr",
    "write-followup-analyze",
    "write-followup-tag"
  ]
}
