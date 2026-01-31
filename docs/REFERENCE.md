# Associative Memory — Agent Reference Card

**Purpose:** Persistent memory for documents with semantic search.

**Default store:** `.assocmem/` at git repo root (auto-created)

**Key principle:** The schema is data. System documents control behavior and can be queried/updated.

## Python API
```python
from assocmem import AssociativeMemory, Item
mem = AssociativeMemory()  # uses default store

# Core indexing
mem.update(uri, source_tags={})          # Index document from URI → Item
mem.remember(content, id=None, ...)      # Index inline content → Item

# Search
mem.find(query, limit=10)                # Semantic search → list[Item]
mem.find_similar(uri, limit=10)          # Similar items → list[Item]
mem.query_tag(key, value=None)           # Tag lookup → list[Item]
mem.query_fulltext(query)                # Text search → list[Item]

# Item access
mem.get(id)                              # Fetch by ID → Item | None
mem.exists(id)                           # Check existence → bool
mem.list_collections()                   # All collections → list[str]

# Context & top-of-mind (for agent handoff)
mem.set_context(summary, ...)            # Set working context
mem.get_context()                        # Get working context → WorkingContext
mem.top_of_mind(hint=None, limit=5)      # Relevance + recency → list[Item]
mem.recent(limit=10, since=None)         # Just recent items → list[Item]
mem.list_topics()                        # Active topics → list[str]
mem.get_topic_summary(topic)             # Topic overview → TopicSummary

# System documents (schema as data)
mem.get_routing()                        # Get routing config → RoutingContext
mem.get_system_document(name)            # Get _system:{name} → Item | None
mem.list_system_documents()              # All system docs → list[Item]
```

## Item Fields
`id`, `summary`, `tags` (dict), `score` (searches only)

Timestamps accessed via properties: `item.created`, `item.updated` (read from tags)

## System Tags (auto-managed)
`_created`, `_updated`, `_updated_date`, `_content_type`, `_source`
`_session`, `_topic`, `_level`, `_summarizes`, `_system`, `_visibility`, `_for`

**System tags cannot be set by source_tags or generated tags** — they are managed by the system.

```python
mem.query_tag("_updated_date", "2026-01-30")  # Temporal query
mem.query_tag("_source", "inline")            # Find remembered content
mem.query_tag("_system", "true")              # All system documents
```

**Note:** Relevance/focus scores are computed at query time, not stored.
This preserves agility between broad exploration and focused work.

## System Documents
The schema is data. Behavior is controlled by documents in the store:

| Document | Purpose |
|----------|---------|
| `_system:routing` | Private/shared routing patterns |
| `_system:context` | Current working context |
| `_system:guidance` | Local behavioral guidance |
| `_system:guidance:{topic}` | Topic-specific guidance |

```python
# Query and update system documents like any item
guidance = mem.get_system_document("guidance:code_review")

# Create/update guidance through remember()
mem.remember(
    content="For code review: check security, tests, docs",
    id="_system:guidance:code_review",
    source_tags={"_system": "true"}
)
```

## Agent Session Pattern
```python
# New session starts
ctx = mem.get_context()                 # What were we doing?
items = mem.top_of_mind()               # What's relevant now?

# ... work happens ...

# End of session
mem.set_context(
    summary="Finished auth flow. Next: tests.",
    active_items=["file:///src/auth.py"],
    topics=["authentication"]
)
```

## CLI
```bash
assocmem <cmd> [args]
# Commands: find, similar, search, tag, update, get, exists, collections, init
```

## When to Use
- `update()` — when referencing any file/URL worth remembering
- `remember()` — capture conversation insights, decisions, notes
- `find()` — before searching filesystem; may already be indexed
- `top_of_mind()` — at session start for context
- `set_context()` — at session end for handoff

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
