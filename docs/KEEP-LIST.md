# keep list

List recent items, filter by tags, or list tag keys and values.

## Usage

```bash
keep list                             # Recent items (by update time)
keep list -n 20                       # Show 20 most recent
keep list --sort accessed             # Sort by last access time
```

## Options

| Option | Description |
|--------|-------------|
| `-n`, `--limit N` | Maximum results (default 10) |
| `-t`, `--tag KEY=VALUE` | Filter by tag (repeatable, AND logic) |
| `-T`, `--tags=` | List all tag keys |
| `-T`, `--tags=KEY` | List values for a specific tag key |
| `--sort ORDER` | Sort by `updated` (default) or `accessed` |
| `--since DURATION` | Only items updated since (ISO duration or date) |
| `--history` | Include archived versions |
| `-s`, `--store PATH` | Override store directory |

## Tag filtering

```bash
keep list --tag project=myapp         # Items with project=myapp
keep list --tag project               # Items with any 'project' tag
keep list --tag foo --tag bar         # Items with both tags (AND)
keep list --tag project --since P7D   # Combine tag filter with recency
```

## Listing tags

The `--tags` option (note: different from `--tag`) lists tag metadata:

```bash
keep list --tags=                     # List all distinct tag keys
keep list --tags=project              # List all values for 'project' tag
```

## Time filtering

```bash
keep list --since P3D                 # Last 3 days
keep list --since P1W                 # Last week
keep list --since PT1H               # Last hour
keep list --since 2026-01-15         # Since specific date
```

## Including versions

```bash
keep list --history                   # Include archived versions
```

## Pipe composition

```bash
keep --ids list -n 5 | xargs keep get              # Get details for recent items
keep --ids list --tag project=foo | xargs keep del  # Bulk operations
```

## See Also

- [TAGGING.md](TAGGING.md) — Tag system and filtering
- [KEEP-FIND.md](KEEP-FIND.md) — Search by meaning
- [KEEP-GET.md](KEEP-GET.md) — Retrieve full item details
- [REFERENCE.md](REFERENCE.md) — Quick reference index
