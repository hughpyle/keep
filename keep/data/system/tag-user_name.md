---
tags:
  category: system
  context: tag-description
---
# Tag: `user_name` — Observed User Label

The `user_name` tag records a user-facing name or handle observed on a message
or conversation source. For Hermes gateway notes, this is the display name seen
on the messaging platform at capture time.

## Characteristics

- **Machine-populated**: Typically set automatically by integrations such as Hermes.
- **Non-canonical**: Values may vary over time as users rename themselves.
- **Useful for context**: Preserves observed naming variants on the source note.
- **Not an edge tag**: Use `user_id` for stable identity relationships.
