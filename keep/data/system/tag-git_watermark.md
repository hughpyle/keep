---
tags:
  category: system
  context: tag
  _constrained: "false"
  _singular: "true"
---
# .tag/git_watermark

Latest-ingested commit SHA for a git repository directory item. Used by
the daemon's incremental git-history ingest to resume from where it
last stopped — passed to `git log {watermark}..HEAD` to fetch only
new commits.

## Characteristics

- **Singular**: `_singular: "true"` — each directory has exactly one
  current watermark. Writing a new value replaces the old one rather
  than accumulating a list, so the reader can treat it as a scalar.
- **Set automatically** by `ingest_git_history()` after each successful
  ingest pass.
- **Format**: 40-character hex SHA (validated before use; corrupted
  values fall back to a full scan).
