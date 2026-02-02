# Associative Memory — Agent Reference Card

**Purpose:** Persistent memory for documents with semantic search.

**Default store:** `.keep/` at git repo root (auto-created)

**Key principle:** Lightweight but extremely flexible functionality.  A minimal and extensible metaschema.

## CLI
```bash
keep <cmd> [args]
# Commands: find, similar, search, tag, tag-update, update, get, exists, collections, init

# Debug mode
keep -v <cmd>                        # Enable debug logging to stderr

# Search with time filtering (--since accepts ISO duration or date)
keep find "query" --since P7D        # Last 7 days
keep find "query" --since P1W        # Last week
keep find "query" --since PT1H       # Last hour
keep find "query" --since 2026-01-15 # Since specific date
keep similar ID --since P30D         # Similar items from last 30 days
keep search "text" --since P3D       # Full-text search, last 3 days

# Tag commands
keep tag --list                      # List all tag keys
keep tag project                     # Docs with 'project' tag (any value)
keep tag project myapp               # Docs with project=myapp
keep tag project --list              # List values for 'project'
keep tag project --since P7D         # Filter by recency

keep tag-update ID --tag key=value   # Add/update tag
keep tag-update ID --remove key      # Remove tag
keep tag-update ID1 ID2 --tag k=v    # Tag multiple docs
```

## Python API
```python
from keep import Keeper, Item
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
kp.list_collections()                   # All collections → list[str]

# System documents
kp.get_routing()                        # Get routing config → RoutingContext
kp.get_system_document(name)            # Get _system:{name} → Item | None
kp.list_system_documents()              # All system docs → list[Item]
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
4. **User tags** — passed to `update()`, `remember()`, or `tag()`

### Environment Variable Tags
Set tags via environment variables with the `KEEP_TAG_` prefix:
```bash
export KEEP_TAG_PROJECT=myapp
export KEEP_TAG_OWNER=alice
keep remember "deployment note"  # auto-tagged with project=myapp, owner=alice
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
`_created`, `_updated`, `_updated_date`, `_content_type`, `_source`
`_session`, `_topic`, `_level`, `_summarizes`, `_system`, `_visibility`, `_for`

```python
kp.query_tag("_updated_date", "2026-01-30")  # Temporal query
kp.query_tag("_source", "inline")            # Find remembered content
kp.query_tag("_system", "true")              # All system documents
```

**Note:** Relevance/focus scores are computed at query time, not stored.

## System Documents
There is a very small metaschema, managed by documents in the store.  You can adjust these as needed.

| Document | Purpose |
|----------|---------|
| `_system:routing` | Private/shared routing patterns |
| `_system:context` | Current working context |
| `_system:guidance` | Local behavioral guidance |
| `_system:guidance:{topic}` | Topic-specific guidance |

```python
# Query and update system documents like any item
guidance = kp.get_system_document("guidance:code_review")

# Create/update guidance through remember()
kp.remember(
    content="For code review: check security, tests, docs",
    id="_system:guidance:code_review",
    tags={"_system": "true"}
)
```

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

## When to Use
- `update()` — when referencing any file/URL worth remembering
- `remember()` — capture conversation insights, decisions, notes
- `find()` — before searching filesystem; may already be indexed
- `find(since="P7D")` — filter to recent items when recency matters

## Private vs Shared Routing
Items tagged for private visibility route to a **physically separate** store.

**Default private patterns:**
- `{"_visibility": "draft"}`
- `{"_visibility": "private"}`
- `{"_for": "self"}`

Private items cannot be seen from the shared store — physical separation, not convention.

Routing rules live in `_system:routing` document (shared store). Update it to customize.

## Domain Patterns
See [patterns/domains.md](../patterns/domains.md) for organization templates.
See [patterns/conversations.md](../patterns/conversations.md) for process knowledge.
