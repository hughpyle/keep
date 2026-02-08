# Python API Reference

The CLI (`keep put`, `keep find`, etc.) is the primary interface. The Python API is for embedding keep into applications.

## Installation

```bash
pip install keep-skill
```

See [QUICKSTART.md](QUICKSTART.md) for provider configuration (API keys or local models).

## Quick Start

```python
from keep import Keeper

kp = Keeper()  # Uses ~/.keep/ by default

# Index documents
kp.update("file:///path/to/doc.md", tags={"project": "myapp"})
kp.update("https://example.com/page", tags={"topic": "reference"})
kp.remember("Important insight about auth patterns")

# Search
results = kp.find("authentication", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.id}: {r.summary}")

# Retrieve
item = kp.get("file:///path/to/doc.md")
print(item.summary)
print(item.tags)
```

## API Reference

### Imports

```python
from keep import Keeper, Item
from keep.document_store import VersionInfo  # for version history
```

### Initialization

```python
# Default store (~/.keep/)
kp = Keeper()

# Custom store location
kp = Keeper(store_path="/path/to/store")

# Override collection
import os
os.environ["KEEP_COLLECTION"] = "work"
kp = Keeper()  # Uses work collection
```

### Core Operations

#### Indexing

```python
# Index from URI (file:// or https://)
kp.update(uri, tags={}, summary=None) → Item

# Index inline content
kp.remember(content, summary=None, tags={}) → Item

# Notes:
# - If summary provided, skips auto-summarization
# - remember() uses content verbatim if short (≤max_summary_length)
# - User tags (domain, topic, etc.) provide context for summarization
```

#### Search

```python
# Semantic search
kp.find(query, limit=10, since=None) → list[Item]

# Find similar to a document
kp.find_similar(uri, limit=10, since=None) → list[Item]

# Similar items using stored embedding
kp.get_similar_for_display(id, limit=3) → list[Item]

# Tag-based query
kp.query_tag(key, value=None, since=None) → list[Item]

# Full-text search
kp.query_fulltext(query, since=None) → list[Item]

# Time filtering (all search methods support since)
kp.find("auth", since="P7D")      # Last 7 days
kp.find("auth", since="P1W")      # Last week  
kp.find("auth", since="PT1H")     # Last hour
kp.find("auth", since="2026-01-15")  # Since date
```

#### Item Access

```python
# Get by ID
kp.get(id) → Item | None

# Check existence
kp.exists(id) → bool

# List recent items
kp.list_recent(limit=10, sort="updated") → list[Item]
# sort options: "updated" (default), "created", "accessed"

# List collections
kp.list_collections() → list[str]
```

#### Tags

```python
# Update tags only (no re-processing)
kp.tag(id, tags={}) → Item | None

# Delete tag by setting empty value
kp.tag("doc:1", {"obsolete": ""})  # Removes 'obsolete' tag

# List tag keys or values
kp.list_tags(key=None) → list[str]
kp.list_tags()          # All tag keys
kp.list_tags("project") # All values for 'project' key
```

#### Version History

```python
# Get previous version
kp.get_version(id, offset=1) → Item | None
# offset: 0=current, 1=previous, 2=two versions ago

# List all versions
kp.list_versions(id, limit=10) → list[VersionInfo]

# Get navigation metadata
kp.get_version_nav(id) → dict
# Returns: {"prev": [...], "next": [...]}
```

#### Current Intentions (Now)

```python
# Get current intentions (auto-creates if missing)
kp.get_now() → Item

# Set current intentions
kp.set_now(content, tags={}) → Item
```

#### Deletion

```python
# Delete item (reverts to previous version if history exists)
kp.delete(id) → Item | None

# Complete removal (if no history)
# If history exists, archives current version and restores previous
```

## Data Types

### Item

Fields:
- `id` (str) — Document identifier
- `summary` (str) — Human-readable summary
- `tags` (dict[str, str]) — Key-value tags
- `score` (float | None) — Similarity score (search results only)

Properties (from tags):
- `item.created` → datetime | None
- `item.updated` → datetime | None  
- `item.accessed` → datetime | None

### VersionInfo

Fields for version history listings:
- `version` (int) — Version offset (0=current, 1=previous, etc.)
- `summary` (str) — Summary of this version
- `updated` (datetime | None) — When this version was created

## Tags

### Tag Merge Order

When indexing, tags merge in priority order (later wins):
1. Existing tags (from previous version)
2. Config tags (from `[tags]` in keep.toml)
3. Environment tags (from `KEEP_TAG_*` variables)
4. User tags (passed to `update()`, `remember()`, or `tag()`)

### Tag Rules

- **One value per key**: Setting a tag overwrites existing value
- **System tags** (prefixed `_`) cannot be set by users
- **Empty string deletes**: `kp.tag(id, {"key": ""})` removes the tag

### Environment Tags

```python
import os
os.environ["KEEP_TAG_PROJECT"] = "myapp"
os.environ["KEEP_TAG_OWNER"] = "alice"

kp.remember("note")  # Auto-tagged with project=myapp, owner=alice
```

### Config Tags

Add to `keep.toml`:
```toml
[tags]
project = "my-project"
owner = "alice"
```

### Recommended Tags

See [REFERENCE.md](REFERENCE.md#organizing-by-project-and-topic) for details on:
- `project` — Bounded work context
- `topic` — Cross-project subject area
- `act` — Speech-act category (commitment, request, assertion, etc.)
- `status` — Lifecycle state (open, fulfilled, declined, etc.)

### System Tags (Read-Only)

Protected tags (prefix `_`) managed automatically:
- `_created` — ISO timestamp
- `_updated` — ISO timestamp  
- `_updated_date` — Date only (YYYY-MM-DD)
- `_accessed` — ISO timestamp
- `_accessed_date` — Date only (YYYY-MM-DD)
- `_content_type` — MIME type
- `_source` — Origin (inline, file, http)

Query system tags:
```python
kp.query_tag("_updated_date", "2026-01-30")
kp.query_tag("_source", "inline")
```

See [SYSTEM-TAGS.md](SYSTEM-TAGS.md) for complete reference.

## Version Identifiers

Append `@V{N}` to specify version by offset:
- `ID@V{0}` — Current version
- `ID@V{1}` — Previous version
- `ID@V{2}` — Two versions ago

```python
item = kp.get("doc:1@V{1}")  # Get previous version
versions = kp.list_versions("doc:1")
```

## Collections

Separate stores for different contexts:

```python
import os

# Work context
os.environ["KEEP_COLLECTION"] = "work"
kp_work = Keeper()
kp_work.set_now("Working on auth")

# Personal context
os.environ["KEEP_COLLECTION"] = "personal"
kp_personal = Keeper()
kp_personal.set_now("Personal notes")
```

Collections are completely separate. For overlays within a single store, use tags instead.

## Error Handling

```python
from keep import Keeper

try:
    kp = Keeper()
    item = kp.get("nonexistent")
    if item is None:
        print("Item not found")
except Exception as e:
    print(f"Error: {e}")
```

Common errors:
- Missing provider configuration (no API key or local models)
- Invalid URI format
- Embedding dimension mismatch (changing models requires new collection)

## See Also

- [QUICKSTART.md](QUICKSTART.md) — Installation and setup
- [REFERENCE.md](REFERENCE.md) — CLI reference
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
- [ARCHITECTURE.md](ARCHITECTURE.md) — System internals
