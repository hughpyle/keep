# keep data — Export and Import

Backup, restore, and migrate keep stores.

## Export

```bash
keep data export backup.json                          # Export to file (JSON, default)
keep data export backup.json --include-system         # Also include system docs (.tag/*, .meta/*, .now, etc.)
keep data export -                                    # Write to stdout (for piping, JSON only)
keep data export notes/ --format md                   # Markdown mode: one .md file per note in a directory
keep data export notes/ --format md --include-parts   # ...also write analysis parts as <note>/@P{N}.md sidecars
keep data export notes/ --format md --include-versions # ...also write archived versions as <note>/@V{N}.md sidecars
```

Exports all user documents, versions, and parts as JSON. **System documents (dot-prefix ids like `.tag/*`, `.meta/*`, `.now`) are excluded by default** — pass `--include-system` to include them. Embeddings are excluded (they are model-dependent and regenerated on import).

### Markdown mode (`--format md`)

Markdown mode writes a **directory** (not a file) with one `.md` file per note. Each file has flat YAML frontmatter with reserved underscore-prefixed metadata keys (`_id`, `_content_hash`, chain metadata, etc.) plus promoted top-level tags, followed by the note summary as the body. By default analysis **parts** and archived **versions** are skipped — use `--include-parts` / `--include-versions` to emit them as sidecar files (see below), or use JSON mode if you need a single self-contained backup.

The output directory must not exist yet, or must be empty. Filenames mirror the id's path structure for easy browsing, using the `wget -m` convention:

| Note id                                | Path in export dir                                            |
|----------------------------------------|---------------------------------------------------------------|
| `auth-notes`                           | `auth-notes.md`                                               |
| `notes/2024/jan-meeting`               | `notes/2024/jan-meeting.md`                                   |
| `.tag/act/commitment`                  | `.tag/act/commitment.md`                                      |
| `file:///Users/x/README.md`            | `file/Users/x/README.md.md`                                   |
| `https://example.com/docs/guide`       | `https/example.com/docs/guide.md`                             |
| `thread:abc-123@host.com#frag`         | `thread/abc-123@host.com%23frag.md`                           |
| `mailto:foo@bar.com`                   | `mailto/foo@bar.com.md`                                       |

Any RFC 3986 URI scheme (`scheme:body`, with scheme matching `[A-Za-z][A-Za-z0-9+.-]*`) becomes a top-level directory named after the scheme — so all `file://`, `https://`, `thread:`, `mailto:`, `tel:` notes group under their own folders. Inside each component, filesystem-unsafe characters (`:`, `#`, `?`, `\`, `*`, `<`, `>`, `|`, non-ASCII) are percent-encoded; `@`, `+`, `=`, `,`, `(`, `)`, space stay literal because they are valid on every modern filesystem. `.md` is always appended to the last component, even for ids that already end in `.md`, so the suffix is unambiguous. Any single path component that exceeds the filesystem's per-component limit is truncated and disambiguated with a short SHA256 suffix; the full id is always preserved in the file's `_id` frontmatter key.

Markdown mode is intended for human browsing, grep-friendly backups, and handoff to tools that consume markdown-with-frontmatter. For round-trip backup/restore, use JSON mode — `keep data import` only reads the JSON format.

Example output file (`auth-notes.md`):

```markdown
---
_id: auth-notes
_content_hash: abc123
topic: auth
_source: inline
---

Authentication patterns for OAuth2...
```

#### Parts and versions sidecars

When `--include-parts` or `--include-versions` is passed, notes that have analysis parts or archived versions get a sidecar **directory** alongside the parent file:

```
rust-tutorial.md             ← the current note (parent file)
rust-tutorial/               ← sidecar dir (only created if parts/versions exist)
  @P{1}.md                   ← analysis part 1
  @P{2}.md                   ← analysis part 2
  @V{1}.md                   ← previous version (1 step back from current)
  @V{2}.md                   ← 2 steps back
  @V{3}.md                   ← 3 steps back
```

Filenames mirror the in-app navigation ids:

- **`@P{N}.md`** — analysis part with absolute `part_num = N`. Body is the part's text (the `summary` field, same as for notes); frontmatter has the parent `_id`, `_part_num`, `_created`, and any promoted top-level part tags.
- **`@V{N}.md`** — archived version with offset `N` from the current version (`@V{1}` is the most recent prior, `@V{2}` is two steps back, …). The current version stays in the parent file — there is no `@V{0}.md`. Frontmatter has the parent `_id`, `_version_offset` (the `N`), `_version` (the absolute database version number, for reference), `_created`, `_content_hash`, and any promoted top-level version tags. Body is the historical summary.

Notes with no parts or versions get no sidecar dir — `plain-note.md` stays a single flat file even when both flags are on.

If two distinct ids would write to the same path (rare — only happens with deliberately confusing ids like `combo` with parts and a separate `combo/@P{1}` note that also encodes to the same path), the export aborts with a clear error naming both ids.

## Import

```bash
keep data import backup.json                 # Merge: skip existing IDs
keep data import backup.json --mode replace  # Replace: clear store first (prompts for confirmation)
keep data import -                           # Read from stdin
```

### Import Modes

- **merge** (default) — imports new documents, skips any with IDs that already exist in the target store
- **replace** — deletes all existing documents first, then imports (requires confirmation)

### After Import

Imported documents, versions, and parts are queued for re-embedding. Run:

```bash
keep pending    # Process embeddings in background
```

Until embeddings are processed, imported documents are retrievable by ID (`keep get`) and visible in `keep list`, but won't appear in semantic search (`keep find`).

## Export Format

```json
{
  "format": "keep-export",
  "version": 3,
  "exported_at": "2026-02-19T12:00:00",
  "store_info": {
    "document_count": 42,
    "version_count": 120,
    "part_count": 15,
    "collection": "default"
  },
  "documents": [
    {
      "id": "auth-notes",
      "summary": "Authentication patterns for OAuth2...",
      "tags": {"topic": "auth", "_source": "inline"},
      "content_hash": "abc123",
      "created_at": "2026-01-15T10:30:00",
      "updated_at": "2026-02-01T14:22:00",
      "accessed_at": "2026-02-19T09:00:00",
      "versions": [
        {"version": 1, "summary": "...", "tags": {}, "content_hash": "...", "created_at": "..."}
      ],
      "parts": [
        {"part_num": 1, "summary": "...", "tags": {}, "created_at": "..."}
      ]
    }
  ]
}
```

**What's included:** document summaries, tags (including system tags like `_source`), timestamps, version history, structural parts.

**What's excluded:** embeddings (model-dependent, regenerated on import), store configuration (target uses its own).

## Use Cases

- **Backup:** `keep data export backup-$(date +%Y%m%d).json`
- **Migrate local to cloud:** Export from local store, import into hosted store (when supported)
- **Transfer between machines:** Export, copy file, import
- **Merge stores:** Export from one, `keep data import --store /other/path backup.json`

## Python API

```python
from keep import Keeper

kp = Keeper()

# Streaming export — yields header, then one dict per document.
# Each document dict is self-contained: versions and parts are
# included inline (not yielded separately).
for i, chunk in enumerate(kp.export_iter()):
    if i == 0:
        header = chunk   # {"format", "version", "exported_at", "store_info"}
    else:
        doc = chunk      # {"id", "summary", "tags", ..., "versions": [...], "parts": [...]}

# Convenience: collect everything into a single dict (loads all into memory)
data = kp.export_data()                          # All documents
data = kp.export_data(include_system=False)       # Skip system docs

# Import
stats = kp.import_data(data, mode="merge")        # Skip existing
stats = kp.import_data(data, mode="replace")      # Clear first
# stats = {"imported": 10, "skipped": 2, "versions": 5, "parts": 3, "queued": 10}
```
