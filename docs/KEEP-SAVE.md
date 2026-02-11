# keep save

Save now history as a named item, archiving a thread of work.

As you update `keep now` throughout a session, version history accumulates. `keep save` extracts that history into a named item, resetting now for the next thread. With tag filtering, you can save only the versions relevant to a specific project.

## Usage

```bash
keep save "thread-name"                        # Save all now history
keep save "auth-thread" -t project=myapp       # Save only matching versions
keep save "auth-thread" -t project=myapp       # Incremental: appends more
```

## Options

| Option | Description |
|--------|-------------|
| `-t`, `--tag KEY=VALUE` | Only extract versions matching these tags (repeatable) |
| `-s`, `--store PATH` | Override store directory |

## How it works

1. Versions matching the tag filter (or all, if no filter) are moved from now to the named item
2. Non-matching versions remain in now's history, with gaps tolerated
3. If now is fully emptied, it resets to its default content
4. The saved item gets `_saved_from` and `_saved_at` system tags

## Incremental save

Saving to an existing name **appends** the new versions on top of the existing history. This enables incremental archival across sessions:

```bash
# Session 1
keep now "design discussion" -t project=alpha
keep now "decided on approach B" -t project=alpha
keep save "alpha-log" -t project=alpha

# Session 2
keep now "implemented approach B" -t project=alpha
keep now "tests passing" -t project=alpha
keep save "alpha-log" -t project=alpha          # Appends to existing

keep get alpha-log --history                     # Shows all 4 versions
```

## Tag-filtered save

When you work on multiple projects in one session, tag filtering lets you save each thread separately:

```bash
keep now "auth: token refresh" -t project=auth
keep now "docs: update API guide" -t project=docs
keep now "auth: added tests" -t project=auth

keep save "auth-thread" -t project=auth    # Extracts 2 auth versions
keep now                                    # Still has docs version
```

## Version history

The saved item has full version history, navigable like any other item:

```bash
keep get thread-name                 # Current (newest saved)
keep get thread-name -V 1            # Previous version
keep get thread-name --history       # List all versions
```

## See Also

- [KEEP-NOW.md](KEEP-NOW.md) — The nowdoc and intentions tracking
- [VERSIONING.md](VERSIONING.md) — Version history and navigation
- [TAGGING.md](TAGGING.md) — Tag system and filtering
- [REFERENCE.md](REFERENCE.md) — Quick reference index
