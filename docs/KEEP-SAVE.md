# keep save

Save versions from now (or another item) as a named item.

As you update `keep now` throughout a session, version history accumulates. `keep save` extracts selected versions into a named item. Requires either `-t` (tag filter) or `--only` (cherry-pick the tip).

## Usage

```bash
keep save "auth-thread" -t project=myapp       # Save matching versions from now
keep save "auth-thread" -t project=myapp       # Incremental: appends more
keep save "quick-note" --only                   # Move just the current version
keep save "target" --from "source" -t topic=X  # Reorganize between items
```

## Options

| Option | Description |
|--------|-------------|
| `-t`, `--tag KEY=VALUE` | Only extract versions matching these tags (repeatable) |
| `--only` | Move only the current (tip) version |
| `--from ITEM_ID` | Source item to extract from (default: now) |
| `-s`, `--store PATH` | Override store directory |

**Required:** at least one of `-t` or `--only` must be specified.

## How it works

1. Versions matching the filter are moved from the source to the named target
2. Non-matching versions remain in the source, with gaps tolerated
3. If the source is fully emptied and is `now`, it resets to default content
4. The saved item gets `_saved_from` and `_saved_at` system tags

## Cherry-picking with --only

`--only` moves just the current (tip) version, one at a time. This is the cherry-picker for reorganizing untagged items:

```bash
keep save "thread-a" --only          # Move tip to thread-a
keep save "thread-b" --only          # Move next tip to thread-b
keep save "thread-a" --only          # Append another to thread-a
```

Combine with `-t` to only move the tip if it matches:

```bash
keep save "auth-log" --only -t topic=auth   # Move tip only if tagged auth
```

## Reorganizing with --from

Use `--from` to extract versions from any item, not just now:

```bash
# Over-grabbed? Pull specific versions out
keep save "auth-thread" --from "big-dump" -t project=auth
keep save "docs-thread" --from "big-dump" -t project=docs

# Cherry-pick one version from an existing item
keep save "highlights" --from "session-log" --only
```

## Incremental save

Saving to an existing name **appends** the new versions on top of the existing history:

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
