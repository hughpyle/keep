# keep find

Find items by semantic similarity or full-text search.

## Usage

```bash
keep find "authentication"            # Semantic similarity search
keep find "auth" --text               # Full-text search on summaries
keep find --id ID                     # Find items similar to an existing item
```

## Options

| Option | Description |
|--------|-------------|
| `--text` | Use full-text search instead of semantic similarity |
| `--id ID` | Find items similar to this ID (instead of text query) |
| `--include-self` | Include the queried item (only with `--id`) |
| `-t`, `--tag KEY=VALUE` | Filter by tag (repeatable, AND logic) |
| `-n`, `--limit N` | Maximum results (default 10) |
| `--since DURATION` | Only items updated since (see time filtering below) |
| `--until DURATION` | Only items updated before (see time filtering below) |
| `-H`, `--history` | Include archived versions of matching items |
| `-a`, `--all` | Include hidden system notes (IDs starting with `.`) |
| `-s`, `--store PATH` | Override store directory |

## Semantic vs full-text search

**Semantic search** (default) finds items by meaning using embeddings. "authentication" matches items about "login", "OAuth", "credentials" even if they don't contain the word "authentication".

**Full-text search** (`--text`) matches exact words in summaries. Faster but literal.

## Similar-to-item search

Find items similar to an existing document:

```bash
keep find --id file:///path/to/doc.md           # Similar to this document
keep find --id %a1b2c3d4                        # Similar to this item
keep find --id %a1b2c3d4 --since P30D           # Similar items from last 30 days
```

## Tag filtering

Combine semantic search with tag filters (AND logic):

```bash
keep find "auth" -t project=myapp               # Search within a project
keep find "auth" -t project -t topic=security    # Multiple tags (AND)
```

Tag filters are applied as **pre-filters on the vector search** — results are the best matches *within* the filtered set, not global results filtered afterward. This makes tags suitable for data isolation (per-user, per-project, etc.). See [TAGGING.md](TAGGING.md#tag-based-isolation).

## Time filtering

The `--since` and `--until` options accept ISO 8601 durations or dates:

```bash
keep find "auth" --since P7D           # Last 7 days
keep find "auth" --since P1W           # Last week
keep find "auth" --since PT1H          # Last hour
keep find "auth" --since P1DT12H       # 1 day 12 hours
keep find "auth" --since 2026-01-15    # Since specific date
keep find "auth" --until 2026-02-01    # Before specific date
keep find "auth" --since P30D --until P7D  # Between 30 and 7 days ago
```

## Including archived versions

```bash
keep find "auth" --history             # Also search old versions of items
```

## See Also

- [KEEP-LIST.md](KEEP-LIST.md) — List and filter by tags
- [KEEP-GET.md](KEEP-GET.md) — Retrieve full item details
- [TAGGING.md](TAGGING.md) — Tag filtering patterns
- [REFERENCE.md](REFERENCE.md) — Quick reference index
