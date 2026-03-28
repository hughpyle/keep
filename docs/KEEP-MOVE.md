# keep move

Move versions from now (or another note) into a named note.

As you update `keep now` throughout a session, a string of versions accumulates. `keep move` moves those versions into a named note.

## Usage

```bash
keep move "auth-string"                        # Move versions from now
keep move "target" --source "other-note"       # Move from a different source
```

## Options

| Option | Description |
|--------|-------------|
| `NAME` | Required positional — target note name |
| `--source ID` | Source note to move from (default: `now`) |

## How it works

1. Versions are moved from the source to the named target
2. If the source is fully emptied and is `now`, it resets to default content
3. The moved item gets `_saved_from` and `_saved_at` system tags

**Note on URI-shaped target names:** The target name is just a string ID — it can be anything, including a URI like `https://example.com/doc` or `file:///path/to/file`. However, if the target name looks like a URI, a subsequent `keep put <that-uri>` will re-fetch content from the URL and overwrite what was moved there. This is by design (the ID is its fetch source), but be aware that `move` can effectively create an item whose ID points to a different origin than its content. This is analogous to a redirect — the stored content came from `now`, but the ID says `https://...`.

## Incremental move

Moving to an existing name **appends** the new versions on top of the existing history:

```bash
# Session 1
keep now "design discussion"
keep now "decided on approach B"
keep move "alpha-log"

# Session 2
keep now "implemented approach B"
keep now "tests passing"
keep move "alpha-log"                            # Appends to existing

keep get alpha-log --history                     # Shows all 4 versions
```

## Version history

The moved item has full version history, navigable like any other item:

```bash
keep get string-name                 # Current (newest moved)
keep get string-name -V 1            # Previous version
keep get string-name --history       # List all versions
```

## See Also

- [KEEP-NOW.md](KEEP-NOW.md) — The nowdoc and intentions tracking
- [VERSIONING.md](VERSIONING.md) — Version history and navigation
- [TAGGING.md](TAGGING.md) — Tag system and filtering
- [REFERENCE.md](REFERENCE.md) — Quick reference index
