# Reflective Memory — Agent Reference Card

**Purpose:** Persistent memory for documents with semantic search.

**Default store:** `~/.keep/` in user home (auto-created)

**Key principle:** Lightweight but extremely flexible functionality.  A minimal and extensible metaschema.

## Global Flags

```bash
keep --json <cmd>   # Output as JSON
keep --ids <cmd>    # Output only versioned IDs (for piping)
keep --full <cmd>   # Output full YAML frontmatter
keep -v <cmd>       # Enable debug logging to stderr
```

## Output Formats

Three output formats, consistent across all commands:

### Default: Summary Lines
One line per item: `id@V{N} date summary`
```
file:///path/to/doc.md@V{0} 2026-01-15 Document about authentication patterns...
%a1b2c3d4@V{0} 2026-01-14 URI detection should use proper scheme validation...
```

### With `--ids`: Versioned IDs Only
```
file:///path/to/doc.md@V{0}
%a1b2c3d4@V{0}
```

### With `--full`: YAML Frontmatter
Full details with tags, similar items, and version navigation:
```yaml
---
id: file:///path/to/doc.md
tags: {project: myapp, status: reviewed}
similar:
  - doc:related-auth@V{0} (0.89) 2026-01-14 Related authentication...
  - doc:token-notes@V{0} (0.85) 2026-01-13 Token handling notes...
meta/todo:
  - %a1b2c3d4 Update auth docs for new flow
score: 0.823
prev:
  - @V{1} 2026-01-14 Previous summary text...
---
Document summary here...
```

**Note:** `keep get` and `keep now` default to full format since they display a single item.

### With `--json`: JSON Output
```json
{"id": "...", "summary": "...", "tags": {...}, "score": 0.823}
```

Version numbers are **offsets**: @V{0} = current, @V{1} = previous, @V{2} = two versions ago.

**Output width:** Summaries are truncated to fit the terminal. When stdout is not a TTY (e.g., piped through hooks), output uses 200 columns for wider summaries.

### Pipe Composition

```bash
keep --ids find "auth" | xargs keep get              # Get full details for all matches
keep --ids list -n 5 | xargs keep get                # Get details for recent items
keep --ids list --tag project=foo | xargs keep tag-update --tag status=done
keep --json --ids find "query"                       # JSON array: ["id@V{0}", ...]

# Version history composition
keep --ids now --history | xargs -I{} keep get "{}"  # Get all versions
diff <(keep get doc:1) <(keep get "doc:1@V{1}")      # Diff current vs previous
```

## CLI
```bash
keep                                 # Show current working intentions
keep --help                          # Show all commands

# Current intentions (now)
keep now                             # Show current intentions with version nav
keep now "What's important now"      # Update intentions
keep now -n 10                       # Show with more similar/meta items
echo "piped content" | keep now      # Set from stdin
keep now -V 1                        # Previous intentions
keep now --history                   # List all versions
keep reflect                         # Deep structured reflection practice

# Add or update documents
keep put "inline text" -t topic=auth  # Text mode (content-addressed ID)
keep put file:///path/to/doc.pdf      # URI mode
keep put -                            # Stdin mode
keep put "note" --suggest-tags        # Show tag suggestions from similar items

# Get with versioning and similar items
keep get ID                          # Current version with similar items
keep get ID1 ID2 ID3                 # Multiple items
keep get ID -V 1                     # Previous version with prev/next nav
keep get "ID@V{1}"                   # Same as -V 1 (version identifier syntax)
keep get ID --history                # List all versions (default 10, -n to override)
keep get ID --similar                # List similar items (default 10)
keep get ID --similar -n 20          # List 20 similar items
keep get ID --meta                   # List meta items
keep get ID --meta -n 5              # List 5 meta items per section

# List recent items
keep list                            # Show 10 most recent (summary lines)
keep list -n 20                      # Show 20 most recent
keep --ids list                      # IDs only (for piping)
keep --full list                     # Full YAML frontmatter
keep list --history                  # Include archived versions

# Debug mode
keep -v <cmd>                        # Enable debug logging to stderr

# Search (semantic by default, --text for full-text)
keep find "query"                    # Semantic similarity search
keep find "query" --text             # Full-text search on summaries
keep find --id ID                    # Find similar to item
keep find "query" --history          # Include archived versions
keep find "query" --since P7D        # Last 7 days
keep find "query" --since P1W        # Last week
keep find "query" --since PT1H       # Last hour
keep find "query" --since 2026-01-15 # Since specific date
keep find --id ID --since P30D       # Similar items from last 30 days

# Tag filtering
keep list --tags=                    # List all tag keys
keep list --tags=project             # List values for 'project' tag
keep list --tag project=myapp        # Find docs with project=myapp
keep list --tag project --since P7D  # Filter by tag and recency
keep list --tag foo --tag bar        # Items with both tags

# Tag filtering on search commands
keep find "auth" -t project=myapp    # Semantic search + tag filter
keep find "auth" -t project -t done  # Multiple tags (AND logic)
keep get ID -t project=myapp         # Verify item has tag (error if not)
keep now -t project=myapp            # Find recent now version with tag

keep tag-update ID --tag key=value   # Add/update tag
keep tag-update ID --remove key      # Remove tag
keep tag-update ID1 ID2 --tag k=v    # Tag multiple docs

# Delete / revert
keep del ID                       # Remove item (or revert to previous version)

# Maintenance
keep reindex                      # Rebuild search index with current embedding provider
keep reindex -y                   # Skip confirmation
```

## Python API

See [PYTHON-API.md](PYTHON-API.md) for complete Python API reference.

Quick example:
```python
from keep import Keeper
kp = Keeper()
kp.remember("note", tags={"project": "myapp"})
results = kp.find("authentication", limit=5)
```

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
keep put "deployment note"  # auto-tagged with project=myapp, owner=alice
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

### Organizing by Project and Topic

Two tags help organize work across boundaries:

| Tag | Scope | Examples |
|-----|-------|----------|
| `project` | Bounded work context | `myapp`, `api-v2`, `migration` |
| `topic` | Cross-project subject area | `auth`, `testing`, `performance` |

**Usage patterns:**
```bash
# Project-specific knowledge
keep put "OAuth2 with PKCE chosen" -t project=myapp -t topic=auth

# Cross-project knowledge (topic only)
keep put "Token refresh needs clock sync" -t topic=auth

# Search within a project
keep find "authentication" -t project=myapp

# Search across projects by topic
keep find "authentication" -t topic=auth

# Big picture (no project filter)
keep find "recent work" --since P1D
```

### Speech-Act Tags

Two tags make the commitment structure of work visible:

**`act` — speech-act category:**

| Value | What it marks | Example |
|-------|---------------|---------|
| `commitment` | A promise to act | "I'll fix auth by Friday" |
| `request` | Asking someone to act | "Please review the PR" |
| `offer` | Proposing to act | "I could refactor the cache" |
| `assertion` | A claim of fact | "The tests pass on main" |
| `assessment` | A judgment | "This approach is risky" |
| `declaration` | Changing reality | "Released v2.0" |

**`status` — lifecycle state (for commitments, requests, offers):**

| Value | Meaning |
|-------|---------|
| `open` | Active, unfulfilled |
| `fulfilled` | Completed and satisfied |
| `declined` | Not accepted |
| `withdrawn` | Cancelled by originator |
| `renegotiated` | Terms changed |

**Usage:**
```bash
# Track a commitment
keep put "I'll fix the auth bug" -t act=commitment -t status=open -t project=myapp

# Query open commitments and requests
keep list -t act=commitment -t status=open
keep list -t act=request -t status=open

# Mark fulfilled
keep tag-update ID --tag status=fulfilled

# Record an assertion or assessment (no lifecycle)
keep put "The tests pass" -t act=assertion
keep put "This approach is risky" -t act=assessment -t topic=architecture
```

For full details: `keep get .tag/act` and `keep get .tag/status`.

## System Tags (auto-managed)

Protected tags prefixed with `_`. Users cannot modify these directly.

**Implemented:** `_created`, `_updated`, `_updated_date`, `_accessed`, `_accessed_date`, `_content_type`, `_source`

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

### Version Identifiers

Append `@V{N}` to any ID to specify a version by offset:
- `ID@V{0}` — current version
- `ID@V{1}` — previous version
- `ID@V{2}` — two versions ago

```bash
keep get "doc:1@V{1}"          # Previous version of doc:1
keep get "now@V{2}"            # Two versions ago of nowdoc
```

### Version Access
```python
kp.get_version(id, offset=1)   # Previous version
kp.get_version(id, offset=2)   # Two versions ago
kp.list_versions(id)           # All archived versions (newest first)
```

```bash
keep get ID -V 1               # Previous version
keep get "ID@V{1}"             # Same as -V 1
keep get ID --history          # List all versions
keep now -V 2                  # Two versions ago of nowdoc
```

### History Output

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

### Content-Addressed IDs

Text-mode updates use content-addressed IDs for versioning:
```bash
keep put "my note"              # Creates %a1b2c3d4e5f6
keep put "my note" -t done      # Same ID, new version (tag change)
keep put "different note"       # Different ID (new document)
```

Same content = same ID = enables versioning via tag changes.

## Environment Variables

```bash
KEEP_STORE_PATH=/path/to/store       # Override store location
KEEP_CONFIG=/path/to/.keep           # Override config directory
KEEP_TAG_PROJECT=myapp               # Auto-apply tags (any KEEP_TAG_* variable)
KEEP_VERBOSE=1                       # Debug logging to stderr
KEEP_NO_SETUP=1                      # Skip auto-install of tool integrations
```

## When to Use
- `update()` — when referencing any file/URL worth remembering
- `remember()` — capture conversation insights, decisions, notes
- `find()` — before searching filesystem; may already be indexed
- `find(since="P7D")` — filter to recent items when recency matters

## Contextual Summarization

When you provide user tags during indexing, LLM-based summarizers use context from related items to produce more relevant summaries.

**How it works:**
1. When processing pending summaries, the system finds similar items sharing your tags
2. Items with more matching tags rank higher (+20% score boost per additional tag)
3. Top 5 related summaries are passed as context to the LLM
4. The summary highlights relevance to that context

**Tag changes trigger re-summarization:**
```bash
keep put doc.pdf                    # Generic summary
keep put doc.pdf -t domain=practice # Re-queued for contextual summary
```

When tags change (add, remove, or value change), the document is re-queued for summarization with the new context. The existing summary is preserved until the new one is ready.

**Provider note:** Only LLM-based summarizers (anthropic, openai, ollama, mlx) use context. Simple providers (truncate, first_paragraph) ignore it.

## Domain Patterns
See `.domains` for organization templates (`keep get .domains`).
See `.conversations` for process knowledge (`keep get .conversations`).
