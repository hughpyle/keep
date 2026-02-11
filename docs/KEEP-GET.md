# keep get

Retrieve item(s) by ID.

## Usage

```bash
keep get ID                           # Current version with similar items
keep get ID1 ID2 ID3                  # Multiple items (separated by ---)
keep get ID -V 1                      # Previous version
keep get "ID@V{1}"                    # Same as -V 1 (version identifier syntax)
```

## Options

| Option | Description |
|--------|-------------|
| `-V`, `--version N` | Get specific version (0=current, 1=previous) |
| `-H`, `--history` | List all versions (default 10, use `-n` to override) |
| `-S`, `--similar` | List similar items (default 10) |
| `-M`, `--meta` | List meta items |
| `-t`, `--tag KEY=VALUE` | Require tag (error if item doesn't match) |
| `-n`, `--limit N` | Max items for --history, --similar, --meta (default 10) |
| `-s`, `--store PATH` | Override store directory |

## Default output

Single-item commands (`get`, `now`) default to full YAML frontmatter format:

```yaml
---
id: %a1b2c3d4
tags: {project: myapp, topic: auth, type: learning}
similar:
  - %e5f6a7b8 (0.89) 2026-01-14 Related authentication...
  - %c9d0e1f2 (0.85) 2026-01-13 Token handling notes...
meta/learnings:
  - %d3e4f5a6 Token refresh needs clock sync
prev:
  - @V{1} 2026-01-14 Previous summary text...
---
Document summary here...
```

## Multiple IDs

```bash
keep get doc:1 doc:2 doc:3            # Items separated by ---
keep --ids list -n 5 | xargs keep get # Pipe from list
```

## Display modes

```bash
keep get ID --similar                 # Show similar items
keep get ID --similar -n 20           # Show 20 similar items
keep get ID --meta                    # Show meta items
keep get ID --meta -n 5              # Show 5 meta items per section
keep get ID --history                # List all versions
```

## Tag filtering

```bash
keep get ID -t project=myapp          # Error if item doesn't have this tag
```

## See Also

- [VERSIONING.md](VERSIONING.md) — Version identifiers and history
- [KEEP-FIND.md](KEEP-FIND.md) — Search for items by meaning
- [META-DOCS.md](META-DOCS.md) — How meta sections work
- [REFERENCE.md](REFERENCE.md) — Quick reference index
