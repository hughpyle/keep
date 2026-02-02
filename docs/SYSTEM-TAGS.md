# System Tags Reference

System tags are automatically managed metadata prefixed with underscore (`_`). Users cannot set or modify these tags directly - they are protected by the `filter_non_system_tags()` function in `keep/types.py`.

## Implemented Tags

These tags are actively set and maintained by the system.

### `_created`

**Purpose:** ISO 8601 timestamp of when the item was first indexed.

**Set by:** `ChromaStore.upsert()` in `store.py`

**Behavior:** Set once on first insert, preserved on updates.

**Example:** `"2026-01-15T10:30:00.123456+00:00"`

**Access:** Read via `item.created` property or `item.tags["_created"]`

---

### `_updated`

**Purpose:** ISO 8601 timestamp of the last modification.

**Set by:** `ChromaStore.upsert()`, `update_summary()`, `update_tags()` in `store.py`

**Behavior:** Updated on every modification (content, summary, or tags).

**Example:** `"2026-02-02T14:45:00.789012+00:00"`

**Access:** Read via `item.updated` property or `item.tags["_updated"]`

---

### `_updated_date`

**Purpose:** Date portion of `_updated` for efficient date-based queries.

**Set by:** `ChromaStore.upsert()` in `store.py`

**Behavior:** Always set alongside `_updated`. Format: `YYYY-MM-DD`

**Example:** `"2026-02-02"`

**Usage:** Used by `--since` filtering in CLI commands.

---

### `_content_type`

**Purpose:** MIME type of the document content.

**Set by:** `Keeper.update()` in `api.py` (only for URI-based documents)

**Behavior:** Set if the document provider returns a content type.

**Example:** `"text/markdown"`, `"text/html"`, `"application/pdf"`

**Note:** Not set for `remember()` (inline content).

---

### `_source`

**Purpose:** How the content was obtained.

**Set by:** `Keeper.update()` and `Keeper.remember()` in `api.py`

**Values:**
- `"uri"` - Content fetched from a URI via `update()`
- `"inline"` - Content provided directly via `remember()`

**Usage:** Query with `kp.query_tag("_source", "inline")` to find remembered content.

---

## Reserved Tags

These tags are defined in `keep/context.py` for future hierarchical context features. They are not actively set by current code but are reserved for planned functionality.

### `_session`

**Purpose:** Identifier of the session that last touched this item.

**Planned use:** Track which agent session created/modified content.

---

### `_topic`

**Purpose:** Primary topic classification.

**Planned use:** Automatic topic clustering and retrieval.

---

### `_level`

**Purpose:** Hierarchy level in the context system.

**Planned values:**
- `0` - Source document
- `1` - Cluster summary
- `2` - Topic summary
- `3` - Context summary

---

### `_summarizes`

**Purpose:** IDs of items that this item summarizes.

**Planned use:** Track summarization hierarchy (e.g., a topic summary links to its source documents).

---

### `_visibility`

**Purpose:** Routing control for private/shared store separation.

**Values:**
- `"draft"` - Routes to private store
- `"private"` - Routes to private store
- (unset) - Routes to shared store

**Defined in:** `RoutingContext` in `context.py`

---

### `_for`

**Purpose:** Routing control for personal items.

**Values:**
- `"self"` - Routes to private store

**Defined in:** `RoutingContext` in `context.py`

---

### `_system`

**Purpose:** Flag indicating a system document (metaschema).

**Values:** `"true"` for system documents

**Usage:** System documents like `_system:routing`, `_system:guidance` use this tag.

---

## Protection Mechanism

System tags are protected from user modification:

```python
# In keep/types.py
SYSTEM_TAG_PREFIX = "_"

def filter_non_system_tags(tags: dict[str, str]) -> dict[str, str]:
    """Filter out any system tags (those starting with '_')."""
    return {k: v for k, v in tags.items() if not k.startswith(SYSTEM_TAG_PREFIX)}
```

This function is called before merging user-provided tags in `update()`, `remember()`, and `tag()` methods.

## Tag Merge Order

When indexing documents, tags are merged in this order (later wins on collision):

1. **Existing tags** - Preserved from previous version
2. **Config tags** - From `[tags]` section in `keep.toml`
3. **Environment tags** - From `KEEP_TAG_*` variables
4. **User tags** - Passed to `update()`, `remember()`, or `tag()`
5. **System tags** - Added/updated by system (cannot be overridden)

## Querying by System Tags

```python
# Find items by source
inline_items = kp.query_tag("_source", "inline")
uri_items = kp.query_tag("_source", "uri")

# Find items by date
today = kp.query_tag("_updated_date", "2026-02-02")

# Find system documents
system_docs = kp.query_tag("_system", "true")
```

## See Also

- [REFERENCE.md](REFERENCE.md) - API reference card
- [QUICKSTART.md](QUICKSTART.md) - Getting started guide
