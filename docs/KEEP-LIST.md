# keep list

List recent items or filter by tags and prefix.

## Usage

```bash
keep list                             # Recent items (by update time)
keep list -n 20                       # Show 20 most recent
keep list --sort accessed             # Sort by last access time
keep list .tag                        # Items with ID prefix ".tag/"
keep list .tag/act                    # Items under ".tag/act/"
```

## Options

| Argument / Option | Description |
|--------|-------------|
| `PREFIX` | Optional positional — filter notes by ID prefix or glob (e.g. `session-*`) |
| `-t`, `--tag KEY=VALUE` | Filter by tag (repeatable, AND logic) |
| `--sort ORDER` | Sort by `updated` (default), `accessed`, `created`, or `id` |
| `-n`, `--limit N` | Maximum results (default 20) |
| `--since DURATION` | Only notes updated since (ISO duration or date) |
| `--until DURATION` | Only notes updated before (ISO duration or date) |
| `-a`, `--all` | Include hidden system notes (IDs starting with `.`) |

## Prefix filtering

```bash
keep list .tag                        # All items under ".tag/"
keep list .tag/act                    # All items under ".tag/act/"
keep list .meta                       # All contextual query definitions
```

Prefix queries always include hidden (dot-prefix) items.

## Tag filtering

```bash
keep list --tag project=myapp         # Items with project=myapp
keep list --tag project               # Items with any 'project' tag
keep list --tag foo --tag bar         # Items with both tags (AND)
keep list --tag project --since P7D   # Combine tag filter with recency
```

## Time filtering

```bash
keep list --since P3D                 # Last 3 days
keep list --since P1W                 # Last week
keep list --since PT1H               # Last hour
keep list --since 2026-01-15         # Since specific date
keep list --until 2026-02-01         # Before specific date
keep list --since P30D --until P7D   # Between 30 and 7 days ago
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
