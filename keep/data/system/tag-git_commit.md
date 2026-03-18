---
tags:
  category: system
  context: tag
  _constrained: "false"
  _inverse: git_file
---
# .tag/git_commit

Git commit that last modified this file. The value is a commit item ID
(e.g. `git://repo#a1b2c3d`). Enables deep search from files to commit
messages and vice versa.

## Characteristics

- **Free-form**: any commit ID (not constrained to a fixed vocabulary)
- **Edge tag**: `_inverse: git_file` creates bidirectional edges between files and commits
- **Set automatically** by git changelog ingest during `keep put -r` on a git repository
