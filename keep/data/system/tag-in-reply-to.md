---
tags:
  category: system
  context: tag-description
  _inverse: has_reply
---
# Tag: `in-reply-to` — Email Reply Threading

The `in-reply-to` tag links an email message to the message it replies to, using the RFC 822 `In-Reply-To` header. It is populated automatically when ingesting email files.

The `_inverse` declaration means this tag creates a navigable relationship edge. If a reply has `in-reply-to: <parent-msg-id>`, then getting the parent shows it under `has_reply:`.

## Characteristics

- **Edge-creating**: The `_inverse: has_reply` declaration makes this an edge tag.
- **Machine-populated**: Set by email extraction during `put`, not typically set manually.
- **Single-valued**: Each email replies to at most one parent message.
