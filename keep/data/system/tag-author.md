---
tags:
  category: system
  context: tag
  _constrained: "false"
  _inverse: authored
---
# .tag/author

The author of this item, identified by email address. Used by git
changelog ingest to link commits to their author. The normalized
email address serves as a join key across sources (git, email, etc.).

## Characteristics

- **Free-form**: any email address (not constrained to a fixed vocabulary)
- **Edge tag**: `_inverse: authored` creates bidirectional edges — from an item to its author, and from an author to their items
- **Set automatically** by git changelog ingest; can also be set manually on any item
