# Tagging

Tags are key-value pairs attached to every item. They enable filtering, organization, and commitment tracking.

**One value per key.** Setting a tag overwrites any existing value for that key.

## Setting tags

```bash
keep put "note" -t project=myapp -t topic=auth    # On create
keep now "working on auth" -t project=myapp        # On now update
keep tag-update ID --tag key=value                 # Add/update tag on existing item
keep tag-update ID --remove key                    # Remove a tag
keep tag-update ID1 ID2 --tag status=done          # Tag multiple items
```

## Tag merge order

When indexing documents, tags are merged in this order (later wins):

1. **Existing tags** — preserved from previous version
2. **Config tags** — from `[tags]` section in `keep.toml`
3. **Environment tags** — from `KEEP_TAG_*` variables
4. **User tags** — passed via `-t` on the command line

### Environment variable tags

Set tags via environment variables with the `KEEP_TAG_` prefix:

```bash
export KEEP_TAG_PROJECT=myapp
export KEEP_TAG_OWNER=alice
keep put "deployment note"    # auto-tagged with project=myapp, owner=alice
```

### Config-based default tags

Add a `[tags]` section to `keep.toml`:

```toml
[tags]
project = "my-project"
owner = "alice"
```

## Tag filtering

The `-t` flag filters results on `find`, `list`, `get`, and `now`:

```bash
keep find "auth" -t project=myapp          # Semantic search + tag filter
keep find "auth" -t project -t topic=auth  # Multiple tags (AND logic)
keep list --tag project=myapp              # List items with tag
keep list --tag project                    # Any item with 'project' tag
keep get ID -t project=myapp              # Error if item doesn't match
keep now -t project=myapp                 # Find now version with tag
```

## Listing tags

```bash
keep list --tags=                    # List all distinct tag keys
keep list --tags=project             # List all values for 'project' tag
```

## Organizing by project and topic

Two tags help organize work across boundaries:

| Tag | Scope | Examples |
|-----|-------|----------|
| `project` | Bounded work context | `myapp`, `api-v2`, `migration` |
| `topic` | Cross-project subject area | `auth`, `testing`, `performance` |

```bash
# Project-specific knowledge
keep put "OAuth2 with PKCE chosen" -t project=myapp -t topic=auth

# Cross-project knowledge (topic only)
keep put "Token refresh needs clock sync" -t topic=auth

# Search within a project
keep find "authentication" -t project=myapp

# Search across projects by topic
keep find "authentication" -t topic=auth
```

For more on these conventions: `keep get .tag/project` and `keep get .tag/topic`.
For domain-specific organization patterns: `keep get .domains`.

## Speech-act tags

Two tags make the commitment structure of work visible:

### `act` — speech-act category

| Value | What it marks | Example |
|-------|---------------|---------|
| `commitment` | A promise to act | "I'll fix auth by Friday" |
| `request` | Asking someone to act | "Please review the PR" |
| `offer` | Proposing to act | "I could refactor the cache" |
| `assertion` | A claim of fact | "The tests pass on main" |
| `assessment` | A judgment | "This approach is risky" |
| `declaration` | Changing reality | "Released v2.0" |

### `status` — lifecycle state

| Value | Meaning |
|-------|---------|
| `open` | Active, unfulfilled |
| `fulfilled` | Completed and satisfied |
| `declined` | Not accepted |
| `withdrawn` | Cancelled by originator |
| `renegotiated` | Terms changed |

### Usage

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

## System tags

Tags prefixed with `_` are protected and auto-managed. Users cannot set them directly.

**Implemented:** `_created`, `_updated`, `_updated_date`, `_accessed`, `_accessed_date`, `_content_type`, `_source`

See [SYSTEM-TAGS.md](SYSTEM-TAGS.md) for complete reference.

## Python API

```python
kp.tag("doc:1", {"status": "reviewed"})      # Add/update tag
kp.tag("doc:1", {"obsolete": ""})            # Delete tag (empty string)
kp.query_tag("project", "myapp")             # Exact key=value match
kp.query_tag("project")                      # Any doc with 'project' tag
kp.list_tags()                               # All distinct tag keys
kp.list_tags("project")                      # All values for 'project'
```

See [PYTHON-API.md](PYTHON-API.md) for complete Python API reference.

## See Also

- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Auto-managed system tags
- [KEEP-LIST.md](KEEP-LIST.md) — List and filter by tags
- [KEEP-FIND.md](KEEP-FIND.md) — Search with tag filters
- [REFERENCE.md](REFERENCE.md) — Quick reference index
