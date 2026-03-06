---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Write followup: OCR extraction for documents flagged with ocr_pages"
---
{
  "followups": [
    {
      "task_type": "ocr",
      "when": {
        "param_true": "processing.ocr"
      },
      "content": "",
      "metadata": {
        "uri": "$write.uri",
        "ocr_pages": "$write.ocr_pages",
        "content_type": "$write.content_type"
      }
    }
  ]
}
