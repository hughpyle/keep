# Reflective Memory — Detailed Agent Guide

This guide provides in-depth patterns for using the reflective memory store effectively.

For the practice introduction (why and when), see [../SKILL.md](../SKILL.md).
For quick CLI reference, see [REFERENCE.md](REFERENCE.md).
For complete Python API reference, see [PYTHON-API.md](PYTHON-API.md).

---

## Overview

The reflective memory provides persistent storage with semantic search.

## Quick Start (Agent Reference)

**CLI:**
```bash
# Uses ~/.keep/ by default (or KEEP_STORE_PATH)
keep put "file://$(keep config tool)/docs/library/ancrenewisse.pdf"
keep put https://inguz.substack.com/p/keep -t topic=practice
keep put "User prefers OAuth2 with PKCE" -t topic=auth
keep find "authentication flow" --limit 5
keep find "auth" -t project=myapp              # Semantic search + tag filter
keep list --tag project=myapp
keep get file:///project/readme.md
keep get ID -t project=myapp                    # Verify item has tag
keep now -t project=myapp                       # Find now version with tag
keep del ID                                   # Remove item or revert to previous version
```

**Python API:**
```python
from keep import Keeper, Item

# Initialize (defaults to ~/.keep/)
kp = Keeper()

# Index a document from file or URL (fetches, embeds, summarizes, tags automatically)
item = kp.update("file:///project/readme.md", tags={"project": "myapp"})
item = kp.update("https://inguz.substack.com/p/keep", tags={"topic": "practice"})

# Store inline content via API (conversations, notes, insights)
kp.remember(
    content="User prefers OAuth2 with PKCE for auth. Discussed tradeoffs.",
    id="conversation:2026-01-30:auth",
    tags={"topic": "authentication"}
)

# Semantic search
results: list[Item] = kp.find("authentication flow", limit=5)
for item in results:
    print(f"{item.score:.2f} {item.id}")
    print(f"  {item.summary}")

# Find similar to existing item
similar = kp.find_similar("file:///project/readme.md", limit=3)

# Tag-based lookup (including system tags for temporal queries)
docs = kp.query_tag("project", "myapp")
today = kp.query_tag("_updated_date", "2026-01-30")

# Check if indexed
if kp.exists("file:///project/readme.md"):
    item = kp.get("file:///project/readme.md")
```

**Item fields:** `id` (URI or custom), `summary` (str), `tags` (dict), `score` (float, search results only). Timestamps are in tags: `item.created` and `item.updated` are property accessors.

**Prerequisites:** Python 3.11+, `uv tool install keep-skill` (with API key) or `uv tool install 'keep-skill[local]'` (no API needed)

**Default store location:** `~/.keep/` in the user's home directory (created automatically). Override with `KEEP_STORE_PATH` or explicit path argument.

**Patterns documentation** (bundled system docs, access via `keep get`):
- `.domains` — domain-specific organization (software dev, research, etc.)
- `.conversations` — process knowledge: how work proceeds
- `.tag/act` — speech-act categories (commitment, request, offer, assertion, assessment, declaration)
- `.tag/status` — lifecycle states (open, fulfilled, declined, withdrawn, renegotiated)
- `.tag/project` — bounded work contexts
- `.tag/topic` — cross-cutting subject areas

**When to use:**
- CLI: `keep put` for all content (URI, inline text, or stdin)
- API: `kp.update()` for URIs, `kp.remember()` for inline content
- `find()` before filesystem search — the answer may already be indexed
- `get_now()` at session start to see current working context
- `set_now()` when focus changes to help future agents

---

## The Practice

This guide assumes familiarity with the reflective practice in [SKILL.md](../SKILL.md). The key points:

**Reflect before acting:** Check your current work context and intentions.
- What kind of conversation is this? (Action? Possibility? Clarification?)
- What do I already know?
```bash
keep now                    # Current context
keep find "this situation"  # Prior knowledge
```

**While acting:** Is this leading to harm? If yes: give it up.

**Reflect after acting:** What happened? What did I learn?
```bash
keep put "what I learned" -t type=learning
```

**Periodically:** Run a full structured reflection:
```bash
keep reflect
```
This guides you through gathering context, examining actions, recognizing conversation structures, and updating intentions.

This cycle — reflect, act, reflect — is the mirror teaching. Memory isn't storage; it's how you develop skillful judgment.

---

## Reading the Output

CLI commands produce structured output. Understanding this format lets you navigate effectively.

### Search results

`keep find "reflection"` returns one line per result — `id date summary`:

```
now 2026-02-07 Finished reading MN61. The mirror teaching: ...
file:///.../library/mn61.html 2026-02-07 The Exhortation to Rāhula...
https://inguz.substack.com/p/keep 2026-02-07 Keep: A Reflective Memory...
file:///.../library/han_verse.txt 2026-02-07 Han Verse: Great is the matter...
```

Each ID in the results can be passed directly to `keep get`.

### Full output (frontmatter format)

`keep get`, `keep now`, and `keep put` produce YAML frontmatter followed by the document body:

```
---
id: file:///.../library/mn61.html
tags: {_source: uri, _updated: 2026-02-07T15:14:28, topic: reflection, type: teaching}
similar:
  - https://inguz.substack.com/p/keep (0.47) 2026-02-07 Keep: A Reflective Memory...
  - now (0.45) 2026-02-07 Finished reading MN61. The mirror teachi...
  - file:///.../library/han_verse.txt (0.44) 2026-02-07 Han Verse: Great is the matter...
prev:
  - @V{1} 2026-02-07 Previous version summary...
---
The Exhortation to Rāhula at Mango Stone is a Buddhist sutra that teaches...
```

**Field reference:**

| Field | Meaning | How to use |
|-------|---------|------------|
| `id` | Document identifier (URI, content hash, or system ID) | Pass to `keep get ID` |
| `tags` | User tags + system tags (`_created`, `_updated`, `_source`, etc.) | Filter with `--tag key=value` |
| `similar` | Related items with similarity scores (0–1) | `keep get <similar-id>` to follow links |
| `prev` | Older versions, shown as `@V{N}` offsets | `keep get ID -V 1` for previous version |
| `next` | Newer versions (shown when viewing an older version) | `keep get ID -V 0` to return to current |

**Navigating from output:**
- See an interesting similar item? → `keep get <that-id>`
- Want the previous version? → `keep get ID -V 1` (the `@V{1}` offset)
- `@V{N}` is a version offset: 0 = current, 1 = previous, 2 = two versions ago

### Version history

`keep now --history` or `keep get ID --history` shows a compact version list:

```
v0 (current): Finished reading MN61. The mirror teaching: reflect before, ...

Archived:
  v1 (2026-02-07): Reading the first teachings. Exploring MN61 and th...
```

### Other formats

- `--json` — machine-readable JSON output
- `--ids` — bare IDs only (useful for piping)

---

## Working Session Pattern

Use the nowdoc as a scratchpad to track where you are in the work. This isn't enforced structure — it's a convention that helps you (and future agents) maintain perspective.

**Session lifecycle (CLI):**
```bash
# 1. Starting work — check your current work context and intentions
keep now                                    # What am I working on? What kind of conversation is this?

# 2. Update context as work evolves (tag by project and topic)
keep now "Diagnosing flaky test in auth module" -t project=myapp -t topic=testing
keep now "Found timing issue" -t project=myapp -t state=investigating

# 3. Check previous context if needed
keep now -V 1                               # Previous version
keep now --history                          # List all versions
keep now -t project=myapp                   # Find recent now with project tag

# 4. Record learnings (cross-project knowledge uses topic only)
keep put "Flaky timing fix: mock time instead of real assertions" -t topic=testing -t type=learning
```

**Python API equivalent:**
```python
# 1. Starting work — check your current work context and intentions
now = kp.get_now()
print(now.summary)  # What am I working on? What kind of conversation is this?

# 2. Update context as work evolves (tag by project and topic)
kp.set_now(
    "Diagnosing flaky test in auth module. Likely timing issue.",
    tags={"project": "myapp", "topic": "testing", "state": "investigating"}
)

# 3. Check previous context if needed
prev = kp.get_version("now", offset=1)  # Previous version
versions = kp.list_versions("now")       # All versions

# 4. Record cross-project learning (topic only, no project)
kp.remember(
    content="Flaky timing in CI → mock time instead of real assertions.",
    tags={"topic": "testing", "type": "learning"}
)
kp.set_now("Completed flaky test fix.", tags={"project": "myapp", "state": "completed"})
```

**Key insight:** The store remembers across sessions; working memory doesn't. When you resume, read context first. All updates create version history automatically.

---

## Agent Handoff Pattern

**Starting a session (CLI):**
```bash
keep now                              # Check current intentions with version history
keep now --history                    # See how intentions evolved
keep find "recent work" --since P1D   # Last 24 hours
```

**Ending a session (CLI):**
```bash
keep now "Completed OAuth2 flow. Token refresh working. Next: add tests." -t topic=auth
```

**Python API equivalent:**
```python
# Starting a session
now = kp.get_now()
print(f"Current focus: {now.summary}")
recent = kp.find("", limit=5, since="P1D")  # Last 24 hours

# Ending a session
kp.set_now(
    "Completed OAuth2 flow. Token refresh working. Next: add tests.",
    tags={"topic": "authentication"}
)
```

**Recent items retrieval (CLI):**
```bash
keep find "authentication" --since P7D   # Last week
keep tag _updated_date=2026-01-30        # Items updated today
```

**Python API:**
```python
recent = kp.find("", since="P1D")                    # Last day
auth_items = kp.find("authentication", since="P7D") # Last week
today = kp.query_tag("_updated_date", "2026-01-30") # Today
```

---

## Breakdowns as Learning

When the normal flow is interrupted — expected response doesn't come, ambiguity surfaces — an assumption has been revealed. **First:** complete the immediate conversation. **Then record:**

```bash
keep put "Assumed user wanted full rewrite. Actually: minimal patch." -t type=breakdown
```

Breakdowns are how agents learn.

---

## Data Model

An item has:
* A unique identifier (URI or custom ID for inline content)
* A `created` timestamp (when first indexed)
* An `updated` timestamp (when last indexed)
* A summary of the content, generated when indexed
* A collection of tags (`{key: value, ...}`)
* Version history (previous versions archived automatically on update)

The full original document is not stored in this service.

**Speech-act tags:** Items can be classified by speech-act type (`act=commitment`, `act=request`, etc.) and tracked through a lifecycle (`status=open` → `status=fulfilled`). This makes the commitment structure of work visible and queryable. See `keep get .tag/act` for details.

The services that implement embedding, summarization and tagging are configured at initialization time. This skill itself is provider-agnostic.

**Contextual Summarization:**

When you provide user tags (domain, topic, project, etc.) during indexing, LLM-based summarizers use context from related items to produce more relevant summaries. Tags aren't just for organization — they shape how new items are understood.

For example, indexing a document with `-t domain=practice` will produce a summary highlighting what's relevant to that practice context, drawing on existing items tagged with the same domain.

Changing tags on an existing document triggers re-summarization with the new context. Simple providers (truncate, first_paragraph) ignore context and produce generic summaries.

## Document Versioning

All documents retain version history automatically. When you update a document, the previous version is archived.

**CLI:**
```bash
keep get ID                   # Current version with similar items
keep get ID --no-similar      # Just the document, no similar
keep get ID --similar         # List similar items (default 10, -n to override)
keep get ID -V 1              # Previous version
keep get ID -V 2              # Two versions ago
keep get ID --history         # List all versions (default 10, -n to override)

keep now -V 1                 # Previous nowdoc
keep now --history            # Nowdoc version history
```

**Python API:**
```python
from keep.document_store import VersionInfo

# Get previous versions
prev = kp.get_version(id, offset=1)      # Previous
two_ago = kp.get_version(id, offset=2)   # Two versions ago

# List all archived versions (newest first)
versions: list[VersionInfo] = kp.list_versions(id, limit=10)
for v in versions:
    print(f"v{v.version}: {v.created_at} - {v.summary[:50]}")

# Get navigation info for display
nav = kp.get_version_nav(id)  # {'prev': [...], 'next': [...]}
```

**Content-addressed IDs for text updates:**
```bash
keep put "my note"              # Creates %a1b2c3d4e5f6
keep put "my note" -t done      # Same ID, new version (tag change)
keep put "different note"       # Different ID (new document)
```

Same content = same ID = enables versioning via tag changes.

## Use Cases

* Keep track of every document or file that is referenced during any task.
* Remember conversation insights, decisions, and notes inline.
* Retrieve by semantic similarity, full-text search, or tags.
* Transfer context between agents or sessions seamlessly.

The data is partitioned by *collection*. Collection names are lowercase ASCII with underscores.

## Functionality

within a collection:

`update(id: URI, tags: dict)` - inserts or updates the document at `URI` into the store.  This process delegates the work of embedding, summarization and tagging, then updates the store.  Any `tags` are merged with existing tags (overlay, not replace) and stored alongside the "derived tags" produced by the tagging service.

> NOTE: update tasks are serialized to avoid any concurrency issues.

`delete(id)` / `revert(id)` - if the item has version history, reverts to the previous version (the current version is discarded). If no history exists, removes the item completely from both stores.

`find(query: str)` - locate items using a semantic-similarity query for any text

`find_similar(id: URI)` - locate nearest neighbors of an existing item using a semantic-similarity query

`query_fulltext(query: str)` - locate using a fulltext search of the *summary* text

`query_tag(key: str, value: str = None)` - locate items that have a given tag, and optionally that have the given value for that tag

### Tagging

There are three domains of tags:

1. **Source tags.**  Key/value pairs provided when calling `update()` or `remember()`.  For example, an object from an AWS bucket has a URI, and also a collection of tags that were applied in AWS.

2. **System tags.**  These have special meaning for this service and are managed automatically.
   * System tags have keys that begin with underscore (`_`).
   * **Source tags and generated tags cannot set system tag values** — any tag starting with `_` in tags is filtered out before storage.
   * The tagging provider does not produce system tags.

   | System Tag | Description | Example |
   |------------|-------------|---------|
   |------------|-------------|---------|
   | `_created` | ISO timestamp when first indexed | `2026-01-30T14:22:00Z` |
   | `_updated` | ISO timestamp when last indexed | `2026-01-30T15:30:00Z` |
   | `_updated_date` | Date portion for easier queries | `2026-01-30` |
   | `_content_type` | MIME type if known | `text/markdown` |
   | `_source` | How content was obtained | `uri`, `inline` |
   | `_session` | Session that last touched this item | `2026-01-30:abc123` |

3. **Generated tags.**  Produced by the tagging provider based on content analysis at index time.

**Temporal queries using system tags (CLI):**
```bash
keep list --tag _updated_date=2026-01-30   # Items updated today
keep list --tag _source=inline             # All inline content
```

**Python API:**
```python
kp.query_tag("_updated_date", "2026-01-30")  # Items updated today
kp.query_tag("_source", "inline")            # All inline content
```

---

## Visibility Conventions

Knowledge has an interior/exterior dimension. Some items are working notes; others are settled knowledge ready to share. This is signaled through **convention**, not enforcement.

**Suggested source tags for visibility:**

| Tag | Values | Meaning |
|-----|--------|---------|
| `_visibility` | `draft`, `private`, `shared`, `public` | Intent for who should see this |
| `_for` | `self`, `team`, `anyone` | Intended audience |
| `_reviewed` | `true`, `false` | Has this been checked before sharing? |

**Example usage:**
```python
# Working hypothesis — routes to private store
kp.remember(
    "I think the bug is in token refresh, but need to verify.",
    tags={"_visibility": "draft", "_for": "self"}
)

# Confirmed learning — routes to shared store
kp.remember(
    "Token refresh fails when clock skew exceeds 30s. Fix: use server time.",
    tags={"_visibility": "shared", "_for": "team", "_reviewed": "true"}
)
```

**Why this matters:**

The shared layer protects the private. When items route to the private store:
- They are physically separate — cannot be seen or deduced from outside
- Working notes, drafts, dead ends stay truly interior
- Settled knowledge graduates to shared through explicit re-tagging

### Physical Separation via Routing

Private isn't just convention — it's enforced by routing to a separate store.

```
Keeper (facade)
    │
    ├── reads: .routing (document in shared store)
    │         ├── summary: "Items tagged draft/private route separately"
    │         └── private_patterns: [{"_visibility": "draft"}, {"_for": "self"}, ...]
    │
    ├── Private Store (physically separate)
    │   └── items matching private_patterns
    │   └── invisible from shared store queries
    │
    └── Shared Store
        └── everything else
        └── includes the .routing document itself
```

The routing context is itself a document with:
- **summary**: Natural language description of the privacy model
- **private_patterns**: Tag patterns that route to private (e.g., `{"_visibility": "draft"}`)
- **metadata**: Additional configuration as needed

The facade reads the routing document and makes physical routing decisions. Queries against the shared store cannot see private items — not by convention, but by physical separation.

**Default private patterns:**
- `{"_visibility": "draft"}`
- `{"_visibility": "private"}`
- `{"_for": "self"}`

**Customizing routing:**
The `.routing` document can be updated to change what routes privately. The patterns are associative — described in the document, not hardcoded.

---

## Domain Patterns

See `.domains` (`keep get .domains`) for suggested collection and tag organization for common use cases:
- Software Development
- Market Research
- Personal Reflection & Growth
- Healthcare Tracking

---

## System Documents

The store's guiding metadata is itself stored as documents — like Oracle's data dictionary stored in tables. This enables agents to query and update the system's behavior.

**Well-known system documents:**

| ID | Purpose | Updatable |
|----|---------|-----------|
| `.routing` | Private/shared routing patterns | Yes |
| `.context` | Current working context | Yes |
| `.guidance` | Local behavioral guidance | Yes |

**Querying system documents:**
```python
# Read the routing configuration
routing = kp.get(".routing")
print(routing.summary)  # Natural language description
print(routing.tags)     # Includes private_patterns

# Find all system documents
system_docs = kp.query_tag("_system", "true")
```

**Updating behavior through documents:**
```python
# User says: "Research best practices for code review and update guidance"

# 1. Agent researches (web, existing patterns, etc.)
# 2. Agent updates the guidance document
kp.remember(
    content="""
    Code Review Guidance (updated based on research):

    - Review for correctness first, style second
    - Small PRs (<400 lines) get better reviews
    - Use checklist: security, error handling, tests, docs
    - Prefer synchronous review for complex changes

    Source: Team retrospective + industry research.
    """,
    id=".guidance/code_review",
    tags={"_system": "true", "domain": "code_review"}
)

# 3. Future sessions read this guidance when doing code review
```

**The pattern:** Agents evolve the system by writing documents, not changing code. This includes:
- Routing rules (what's private vs shared)
- Domain patterns (how to organize for this project)
- Process knowledge (how to do tasks well)
- Local preferences (user-specific guidance)

All queryable. All updateable. All associative.

---

## Implementation Overview

Components:
* A configuration file (toml) for details of the database settings and supporting services (embeddings, summarization, tagging).  This configuration file is written at initialization time.  It contains everything necessary to open the datastore for querying any collection.
* A vector database (ChromaDb).
* An embeddings provider that produces a vectorization of the original content.  This enables associative recall and similarity search.
* A summarization provider that produces accurate short summaries of the original content.  This enables consistent fast recall of summary information.
* A tagging provider that produces structured identifiers that describe the original content.  This enables traditional indexing and navigation strategies.
* A document provider that fetches content from URIs (file://, https://, etc.)

### Provider Options

See [QUICKSTART.md](QUICKSTART.md#provider-options) for available embedding, summarization, and tagging providers. Providers are auto-detected at initialization based on platform and available API keys.

## Error Handling

| Situation | Behavior |
|-----------|----------|
| Store path doesn't exist | Created automatically |
| Collection doesn't exist | Created on first `update()` |
| URI unreachable | `IOError` raised from `update()` |
| Item not found | `get()` returns `None`, `find_similar()` raises `KeyError` |
| No results | Empty list returned |

## Initialization

The store auto-initializes on first use. See [QUICKSTART.md](QUICKSTART.md) for provider setup.

**CLI:**
```bash
# Store created automatically at ~/.keep/
keep put "first note"           # Auto-initializes store
KEEP_STORE_PATH=/path keep put "note"  # Custom location via env
```

**Python API:**
```python
from keep import Keeper

kp = Keeper()                      # Uses ~/.keep/ (auto-creates)
kp = Keeper("/path/to/store")      # Explicit path (auto-creates)
```
