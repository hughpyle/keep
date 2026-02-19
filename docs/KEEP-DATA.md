# keep data — Export and Import

Backup, restore, and migrate keep stores.

## Export

```bash
keep data export backup.json                 # Export to file
keep data export backup.json --exclude-system  # Skip system docs (.tag/*, .conversations, etc.)
keep data export -                           # Write to stdout (for piping)
```

Exports all documents, versions, and parts as JSON. Embeddings are excluded (they are model-dependent and regenerated on import).

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

Imported documents are queued for re-embedding. Run:

```bash
keep pending    # Process embeddings in background
```

Until embeddings are processed, imported documents are retrievable by ID (`keep get`) and visible in `keep list`, but won't appear in semantic search (`keep find`).

## Export Format

```json
{
  "format": "keep-export",
  "version": 1,
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
        {"part_num": 1, "summary": "...", "tags": {}, "content": "...", "created_at": "..."}
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
