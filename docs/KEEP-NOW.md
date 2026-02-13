# keep now

Get or set the current working intentions.

The nowdoc is a singleton document that tracks what you're working on right now. It has full version history, so previous intentions are always accessible.

## Usage

```bash
keep now                              # Show current intentions
keep now "Working on auth flow"       # Update intentions
keep now "Auth work" -t project=myapp # Update with tag
echo "piped content" | keep now       # Set from stdin
```

## Options

| Option | Description |
|--------|-------------|
| `--reset` | Reset to default from system |
| `-V`, `--version N` | Get specific version (0=current, 1=previous) |
| `-H`, `--history` | List all versions |
| `-t`, `--tag KEY=VALUE` | Set tag (with content) or filter (without content) |
| `-n`, `--limit N` | Max similar/meta items to show (default 3) |
| `-s`, `--store PATH` | Override store directory |

## Tag behavior

Tags behave differently depending on mode:

- **With content:** `-t` sets tags on the update
- **Without content:** `-t` filters version history to find the most recent version with matching tags

```bash
keep now "Auth work" -t project=myapp   # Sets project=myapp tag
keep now -t project=myapp               # Finds recent version with that tag
```

## Version navigation

The nowdoc retains full history. Each update creates a new version.

```bash
keep now -V 1              # Previous intentions
keep now -V 2              # Two versions ago
keep now --history         # List all versions
```

Output includes `prev:` navigation for browsing version history. See [VERSIONING.md](VERSIONING.md) for details.

## Similar items and meta sections

When updating (`keep now "..."`), the output surfaces similar items and meta sections as occasions for reflection:

```yaml
---
id: now
tags: {project: myapp, topic: auth}
similar:
  - %a1b2c3d4 OAuth2 token refresh pattern...
meta/todo:
  - %e5f6a7b8 validate redirect URIs
meta/learnings:
  - %c9d0e1f2 Token refresh needs clock sync
---
Working on auth flow
```

## keep move

When a string of work is complete, move the now history into a named note. Requires either `-t` (tag filter) or `--only` (cherry-pick the tip).

```bash
keep move "auth-string" -t project=myapp   # Move matching versions
keep move "quick-note" --only              # Move just the current version
```

Moving to an existing name appends incrementally. Use `--from` to reorganize between items. See [KEEP-MOVE.md](KEEP-MOVE.md) for details.

## keep reflect

Deep structured reflection practice. Guides you through gathering context, examining actions, and updating intentions.

```bash
keep reflect
```

## See Also

- [KEEP-MOVE.md](KEEP-MOVE.md) — Move now history into named items
- [TAGGING.md](TAGGING.md) — Tag system and speech-act tracking
- [VERSIONING.md](VERSIONING.md) — Version history and navigation
- [META-DOCS.md](META-DOCS.md) — How meta sections surface contextual items
- [REFERENCE.md](REFERENCE.md) — Quick reference index
