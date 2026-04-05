---
tags:
  category: system
  context: tag-description
  _inverse: user_id_of
---
# Tag: `user_id` — External User Identity

The `user_id` tag identifies an external user or contact associated with a
conversation note. For Hermes gateway notes, this should be a stable canonical
contact ID such as `contact:telegram:42` or `contact:discord:123456789`.

The `_inverse` declaration means this tag creates a navigable relationship
edge. If a conversation note has `user_id: contact:telegram:42`, then
`get contact:telegram:42` will show that note under `user_id_of:`.

## Characteristics

- **Edge-creating**: The `_inverse: user_id_of` declaration makes this an edge tag.
- **Machine-populated**: Typically set automatically by integrations such as Hermes.
- **Stable target**: Values should be canonical identifiers, not display names.
- **Label-friendly**: Values may use labeled-ref syntax such as `contact:telegram:42[[Alice]]`.
