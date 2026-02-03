# Associative Memory — Agent Reference Card

**Purpose:** Persistent memory for documents with semantic search.

**Default store:** `.keep/` at git repo root (auto-created)

**Key principle:** Lightweight but extremely flexible functionality.  A minimal and extensible metaschema.

## Global Flags

```bash
keep --json <cmd>   # Output as JSON
keep --ids <cmd>    # Output only IDs (for piping to xargs)
keep -v <cmd>       # Enable debug logging to stderr
```

## Output Formats

Default output uses YAML frontmatter:
```yaml
---
id: file:///path/to/doc.md
tags:
  project: myapp
  status: reviewed
score: 0.823
prev:
  - 1: 2026-01-15 Previous summary text...
  - 2: 2026-01-14 Older summary here...
---
Document summary here...
```

When viewing an old version (`-V N`):
```yaml
---
id: file:///path/to/doc.md
version: 2
prev:
  - 1: 2026-01-14 Previous summary...
next:
  - 3: 2026-01-16 Next summary...
---
```

With `--json`:
```json
{"id": "...", "summary": "...", "tags": {...}, "score": 0.823}
```

With `--ids` (one ID per line, for piping):
```
file:///path/to/doc.md
mem:2026-01-15T10:30:00
```

### Pipe Composition

```bash
keep --ids system | xargs keep get
keep --ids find "auth" | xargs keep get
keep --ids tag project=foo | xargs keep tag-update --tag status=done
keep --json --ids find "query"  # JSON array of IDs: ["id1", "id2"]
```

## CLI
```bash
keep                                 # Show current working context
keep --help                          # Show all commands

# Current context (now)
keep now                             # Show current context with version nav
keep now "What's important now"      # Update context
keep now -f context.md -t project=x  # Read content from file with tags
keep now -V 1                        # Previous version
keep now --history                   # List all versions

# Get with versioning
keep get ID                          # Current version with prev nav
keep get ID -V 1                     # Previous version with prev/next nav
keep get ID --history                # List all versions

# List recent items
keep list                            # Show 10 most recent items
keep list -n 20                      # Show 20 most recent items

# Debug mode
keep -v <cmd>                        # Enable debug logging to stderr

# Search with time filtering (--since accepts ISO duration or date)
keep find "query" --since P7D        # Last 7 days
keep find "query" --since P1W        # Last week
keep find "query" --since PT1H       # Last hour
keep find "query" --since 2026-01-15 # Since specific date
keep find --id ID --since P30D       # Similar items from last 30 days
keep search "text" --since P3D       # Full-text search, last 3 days

# Tag commands
keep tag --list                      # List all tag keys
keep tag project                     # List values for 'project' tag
keep tag project=myapp               # Find docs with project=myapp
keep tag project=myapp --since P7D   # Filter by recency

keep tag-update ID --tag key=value   # Add/update tag
keep tag-update ID --remove key      # Remove tag
keep tag-update ID1 ID2 --tag k=v    # Tag multiple docs
```

## Python API
```python
from keep import Keeper, Item
from keep.document_store import VersionInfo  # for version history
kp = Keeper()  # uses default store

# Core indexing
kp.update(uri, tags={}, summary=None)   # Index document from URI → Item
kp.remember(content, summary=None, ...) # Index inline content → Item
# Note: If summary provided, skips auto-summarization
# Note: remember() uses content verbatim if short (≤max_summary_length)

# Search (since: ISO duration like "P7D", "PT1H" or date "2026-01-15")
kp.find(query, limit=10, since=None)       # Semantic search → list[Item]
kp.find_similar(uri, limit=10, since=None) # Similar items → list[Item]
kp.query_tag(key, value=None, since=None)  # Tag lookup → list[Item]
kp.query_fulltext(query, since=None)       # Text search → list[Item]

# Tags
kp.tag(id, tags={})                     # Update tags only → Item | None
kp.list_tags(key=None)                  # List tag keys or values → list[str]

# Item access
kp.get(id)                              # Fetch by ID → Item | None
kp.exists(id)                           # Check existence → bool
kp.list_recent(limit=10)                # Recent items by update time → list[Item]
kp.list_collections()                   # All collections → list[str]

# Version history
kp.get_version(id, offset=1)            # Get previous version (1=prev, 2=two ago) → Item | None
kp.list_versions(id, limit=10)          # List archived versions → list[VersionInfo]
kp.get_version_nav(id)                  # Get prev/next for display → dict

# Current context (now)
kp.get_now()                            # Get current context (auto-creates if missing) → Item
kp.set_now(content, tags={})            # Set current context → Item
```

## Item Fields
`id`, `summary`, `tags` (dict), `score` (searches only)

Timestamps accessed via properties: `item.created`, `item.updated` (read from tags)

## Tags

**One value per key.** Setting a tag overwrites any existing value for that key.

**System tags** (prefixed with `_`) are protected and cannot be set by user tags.

### Tag Merge Order
When indexing documents, tags are merged in this order (later wins):
1. **Existing tags** — preserved from previous version
2. **Config tags** — from `[tags]` section in `keep.toml`
3. **Environment tags** — from `KEEP_TAG_*` variables
4. **User tags** — passed to `update()` (CLI or API), `remember()` (API), or `tag()`

### Environment Variable Tags
Set tags via environment variables with the `KEEP_TAG_` prefix:
```bash
export KEEP_TAG_PROJECT=myapp
export KEEP_TAG_OWNER=alice
keep update "deployment note"  # auto-tagged with project=myapp, owner=alice
```

### Config-Based Default Tags
Add a `[tags]` section to `keep.toml`:
```toml
[tags]
project = "my-project"
owner = "alice"
```

### Tag-Only Updates
Update tags without re-processing the document:
```python
kp.tag("doc:1", {"status": "reviewed"})      # Add/update tag
kp.tag("doc:1", {"obsolete": ""})            # Delete tag (empty string)
```

### Tag Queries
```python
kp.query_tag("project", "myapp")             # Exact key=value match
kp.query_tag("project")                      # Any doc with 'project' tag
kp.list_tags()                               # All distinct tag keys
kp.list_tags("project")                      # All values for 'project'
```

## System Tags (auto-managed)

Protected tags prefixed with `_`. Users cannot modify these directly.

**Implemented:** `_created`, `_updated`, `_updated_date`, `_content_type`, `_source`

```python
kp.query_tag("_updated_date", "2026-01-30")  # Temporal query
kp.query_tag("_source", "inline")            # Find remembered content
```

See [SYSTEM-TAGS.md](SYSTEM-TAGS.md) for complete reference.

## Time-Based Filtering
```python
# ISO 8601 duration format
kp.find("auth", since="P7D")      # Last 7 days
kp.find("auth", since="P1W")      # Last week
kp.find("auth", since="PT1H")     # Last hour
kp.find("auth", since="P1DT12H")  # 1 day 12 hours

# Date format
kp.find("auth", since="2026-01-15")

# Works on all search methods
kp.query_tag("project", since="P30D")
kp.query_fulltext("error", since="P3D")
```

## Document Versioning

All documents retain history on update. Previous versions are archived automatically.

### Version Access
```python
kp.get_version(id, offset=1)   # Previous version
kp.get_version(id, offset=2)   # Two versions ago
kp.list_versions(id)           # All archived versions (newest first)
```

```bash
keep get ID -V 1               # Previous version
keep get ID --history          # List all versions
keep now -V 2                  # Two versions ago of nowdoc
```

### Content-Addressed IDs

Text-mode updates use content-addressed IDs for versioning:
```bash
keep update "my note"              # Creates _text:a1b2c3d4e5f6
keep update "my note" -t done      # Same ID, new version (tag change)
keep update "different note"       # Different ID (new document)
```

Same content = same ID = enables versioning via tag changes.

## When to Use
- `update()` — when referencing any file/URL worth remembering
- `remember()` — capture conversation insights, decisions, notes
- `find()` — before searching filesystem; may already be indexed
- `find(since="P7D")` — filter to recent items when recency matters

## Domain Patterns
See [system/domains.md](system/domains.md) for organization templates.
See [system/conversations.md](system/conversations.md) for process knowledge.
