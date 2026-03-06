---
tags:
  category: system
  context: template
  type: continuation-template
  topic: continuations
  description: "Core write mutation for continuation goal=write"
---
{
  "write_inline": {
    "op": "put_item",
    "fields": [
      "content",
      "uri",
      "id",
      "summary",
      "tags",
      "created_at",
      "force"
    ],
    "require_exactly_one": [
      ["content", "uri"]
    ],
    "set": {
      "queue_background_tasks": false,
      "capture_write_context": true
    }
  }
}
