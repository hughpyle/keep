# Document Versioning

All documents retain history on update. Previous versions are archived automatically.

## Strings

Every note is a **string** — a linear chain of versions that grows each time you update it.
Your working context (`now`) accumulates a string of intentions as you work.
When a string of work is complete, `keep move` extracts matching versions by tag
and strings them into a named note.

This isn't version control (no branches, no merges) and it isn't an append-only log
(you can reorganize). It's the temporal dimension of how knowledge evolved — and
unlike other memory stores, you can re-string it by meaning.

## Version identifiers

Append `@V{N}` to any ID to specify a version by offset:

- `ID@V{0}` — current version
- `ID@V{1}` — previous version
- `ID@V{2}` — two versions ago

```bash
keep get "%a1b2c3d4@V{1}"        # Previous version
keep get "now@V{2}"              # Two versions ago of nowdoc
```

## Accessing versions

```bash
keep get ID -V 1                 # Previous version (equivalent to @V{1})
keep get ID --history            # List all versions
keep now -V 1                   # Previous intentions
keep now --history              # List all nowdoc versions
```

## History output

`--history` shows versions as summary lines with version identifiers:

```
now           2026-01-16 Current summary...
now@V{1}      2026-01-15 Previous summary...
now@V{2}      2026-01-14 Older summary...
```

With `--ids`, outputs version identifiers for piping:

```bash
keep --ids now --history
# now@V{0}
# now@V{1}
# now@V{2}
```

## Version navigation in output

When viewing a single item (`keep get`, `keep now`), the output includes `prev:` and `next:` navigation:

```yaml
---
id: %a1b2c3d4
prev:
  - @V{1} 2026-01-14 Previous summary text...
---
Current summary here...
```

## Content-addressed IDs

Text-mode updates use content-addressed IDs for versioning:

```bash
keep put "my note"              # Creates %a1b2c3d4e5f6
keep put "my note" -t done      # Same ID, new version (tag change)
keep put "different note"       # Different ID (new document)
```

Same content = same ID = enables versioning via tag changes.

## Deleting and reverting

`keep del` has revert semantics when versions exist:

```bash
keep del ID                     # If versions exist: reverts to previous version
                                # If no versions: removes item entirely
```

## Pipe composition

```bash
keep --ids now --history | xargs -I{} keep get "{}"   # Get all versions
diff <(keep get doc:1) <(keep get "doc:1@V{1}")       # Diff current vs previous
```

## Python API

```python
kp.get_version(id, offset=1)    # Previous version
kp.get_version(id, offset=2)    # Two versions ago
kp.list_versions(id)            # All archived versions (newest first)
```

See [PYTHON-API.md](PYTHON-API.md) for complete Python API reference.

## See Also

- [KEEP-GET.md](KEEP-GET.md) — Retrieving items and versions
- [KEEP-NOW.md](KEEP-NOW.md) — Nowdoc version history
- [REFERENCE.md](REFERENCE.md) — Quick reference index
