# Tagging

Tags are key-value pairs attached to every item. They enable filtering, organization, and commitment tracking.

Multiple values per key are allowed. Setting an additional value for the same key adds it (deduplicated), rather than overwriting.
Each key supports up to 512 distinct values.

Some tags are **singular** — new values replace old ones instead of accumulating. The `act` and `status` tags are singular: `keep tag ID --tag status=fulfilled` on a doc with `status=open` results in only `fulfilled`, not both.

## Setting tags

```bash
keep put "note" -t project=myapp -t topic=auth    # On create
keep now "working on auth" -t project=myapp        # On now update
keep tag ID --tag key=value                 # Add/update tag on existing item
keep tag ID --remove key                    # Remove a tag
keep tag ID1 ID2 --tag status=done          # Tag multiple items
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
required = ["user"]                    # Enforce required tags on put()
namespace_keys = ["category", "user"]  # LangGraph namespace mapping
```

The `required` list enforces that specified tag keys must be present on every `put()` call. The `namespace_keys` list configures how LangGraph namespace components map to tag names — see [LANGCHAIN-INTEGRATION.md](LANGCHAIN-INTEGRATION.md).

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

The Python API exposes `list_tags()` directly:

```python
kp.list_tags()           # All distinct tag keys
kp.list_tags("project")  # All values for the 'project' tag
```

From the CLI, list notes that have a tag key set:

```bash
keep list -t project       # Any note with a 'project' tag
keep list -t project=myapp # Notes with project=myapp
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

## Tag-based isolation

Tags on `find` are **pre-filters on the vector search**, not post-filters. When you search with `-t user=alice`, the similarity search only considers notes tagged `user=alice` — you get the best matches *within that scope*, not global results filtered afterward. This makes tags suitable for data isolation.

**Pattern: scoped search with `required_tags`**

```toml
# keep.toml
[tags]
required = ["user"]
```

With this config, every `put()` must include a `user` tag (or it raises `ValueError`). Pair it with tag filters on every search to get per-user isolation:

```python
kp.put("my note", tags={"user": "alice"})         # enforced by required_tags
kp.find("auth", tags={"user": "alice"})            # only searches alice's notes
```

```bash
keep put "my note" -t user=alice
keep find "auth" -t user=alice           # scoped to alice
```

**Note:** `required_tags` enforces tags on writes only. The caller is responsible for passing the same tag filter on reads. Without the filter, `find` searches across all notes.

This pattern works for any isolation key — `user`, `project`, `tenant`, `session`, etc. The [LangChain integration](LANGCHAIN-INTEGRATION.md) automates this: namespace components become tags on both writes and searches.

## Speech-act tags

Two tags — `act` and `status` — make the commitment structure of work visible. These are **constrained tags**: only pre-defined values are accepted.

```bash
# Track a commitment
keep put "I'll fix the auth bug" -t act=commitment -t status=open -t project=myapp

# Query open commitments and requests
keep list -t act=commitment -t status=open
keep list -t act=request -t status=open

# Mark fulfilled
keep tag ID --tag status=fulfilled

# Record an assertion or assessment (no lifecycle)
keep put "The tests pass" -t act=assertion
keep put "This approach is risky" -t act=assessment -t topic=architecture
```

For inline reference: `keep get .tag/act` and `keep get .tag/status`.

## Tag descriptions (`.tag/*`)

Every tag key can have a description document at `.tag/KEY`. These are installed automatically on first use and serve as living documentation:

```bash
keep get .tag/act       # Speech-act categories
keep get .tag/status    # Lifecycle status values
keep get .tag/type      # Content type values
keep get .tag/project   # Project tag conventions
keep get .tag/topic     # Topic tag conventions
```

Tag descriptions have their own summary and embedding, so they participate in semantic search. They also contain structured information that the analyzer uses when auto-tagging parts during `keep analyze`.

### Constrained values

Some tags are **constrained** — only pre-defined values are accepted. When a `.tag/KEY` document has `_constrained: true` in its tags, keep validates that every value you assign has a corresponding sub-document at `.tag/KEY/VALUE`.

```bash
keep put "note" -t act=commitment     # ✓ .tag/act/commitment exists
keep put "note" -t act=blurb          # ✗ ValueError: no .tag/act/blurb
```

The error message lists valid values:

```
Invalid value for constrained tag 'act': 'blurb'. Valid values: assertion, assessment, commitment, declaration, offer, request
```

You can extend constrained tags by creating new sub-documents:

```bash
keep put "Active work in progress." --id .tag/status/working
# Now status=working is accepted
```

### Pattern-constrained values

Some tags are open-ended but still validated against a pattern. When a
`.tag/KEY` document has `_value_regex: '...'`, keep validates each assigned
value against that regular expression.

Unlike `_constrained: true`, pattern-constrained tags do **not** require child
docs at `.tag/KEY/VALUE`. `_constrained` and `_value_regex` are mutually
exclusive on one tagdoc.

For edge tags, regex validation applies to the canonical target note ID, not
the literal surface form of a labeled ref.

```bash
keep put "Investigate restart behavior" --id restart-debug -t frame='debugging?'
keep put "Investigate restart behavior" --id restart-debug -t frame='debugging'
# ✗ ValueError: Invalid value for tag 'frame': 'debugging'. Value must match regex '^.+\?$'
```

The bundled `frame` tag uses this to require note IDs ending in `?`, so
`frame: debugging?` is valid but `frame: debugging` is not.

### Singular values

Some tags are **singular** — at most one value is allowed per key. When a `.tag/KEY` document has `_singular: true` in its tags, new values replace old ones instead of accumulating via set-union.

```bash
keep put "fix auth" -t status=open -t act=commitment
keep tag ID --tag status=fulfilled    # replaces open → fulfilled
keep get ID                           # status: fulfilled (not [open, fulfilled])
```

Providing multiple values for a singular key in one call is an error:

```bash
keep tag ID --tag status=open,fulfilled   # ✗ ValueError: singular tag
```

A tag can be both `_constrained` and `_singular`. The `act` and `status` tags are both — values are validated against sub-documents *and* only one value is kept.

To make a custom tag singular, set `_singular: true` on its tagdoc:

```bash
keep put "$(cat <<'EOF'
---
tags:
  _singular: "true"
---
# Tag: `priority`

Priority level. Only one value at a time.
EOF
)" --id .tag/priority
```

### Bundled tag descriptions

keep ships with these tag descriptions:

| Tag | Constrained | Singular | Values | Purpose |
|-----|:-----------:|:--------:|--------|---------|
| `act` | Yes | Yes | `commitment`, `request`, `offer`, `assertion`, `assessment`, `declaration` | Speech-act category (what the speaker is doing) |
| `status` | Yes | Yes | `open`, `blocked`, `fulfilled`, `declined`, `withdrawn`, `renegotiated` | Lifecycle state of commitments/requests/offers |
| `type` | No | No | `conversation`, `paper`, `vulnerability`, `file`, `person`, `project` | Entity type (graph node label) |
| `kind` | No | No | `learning`, `breakdown`, `gotcha`, `reference`, `teaching`, `meeting`, `pattern`, `possibility`, `decision` | Content classification |
| `project` | No | No | (user-defined) | Bounded work context |
| `topic` | No | No | (user-defined) | Cross-cutting subject area |

Constrained tags (`act`, `status`) also have individual sub-documents (e.g., `.tag/act/commitment`, `.tag/status/open`) that describe each value in detail.

Unconstrained tags (`type`, `project`, `topic`) accept any value. Their descriptions document conventions but don't enforce them.

The bundled `frame` tag is pattern-constrained rather than enumerated: it is an
edge tag whose target note ID must end in `?`.

Some tags also define **edges** — navigable relationships between documents. See [EDGE-TAGS.md](EDGE-TAGS.md) for details.

### How tag docs are injected into LLM prompts

Tag descriptions feed into analysis through two independent paths:

#### 1. Guide context (all tags)

When you pass `-t` to `keep analyze`, the full content of each `.tag/KEY` document is prepended to the analysis prompt as context. This guides the LLM's decomposition — how it splits content into parts and what boundaries it recognizes.

```bash
keep analyze doc:1 -t topic -t project
```

This fetches `.tag/topic` and `.tag/project` descriptions and includes them in the analysis prompt, producing better part boundaries and more consistent tagging. Any tag doc participates in guide context, whether constrained or not.

#### 2. Classification (constrained tags only)

After decomposition, a second LLM pass classifies each part. The `TagClassifier` loads all constrained tag descriptions (those with `_constrained: true`) and assembles a classification prompt from their `## Prompt` sections:

- The **parent doc's** `## Prompt` section (e.g., from `.tag/act`) provides overall guidance for the tag key
- Each **value sub-doc's** `## Prompt` section (e.g., from `.tag/act/commitment`) describes when to assign that specific value

The classifier assigns tags only when confidence exceeds the threshold (default 0.7). Tags without a `## Prompt` section use their full content as a fallback description.

To customize classification behavior, edit the `## Prompt` section in a tag doc — the classifier only sees `## Prompt` content, not the surrounding documentation.

## System tags

Tags prefixed with `_` are protected and auto-managed. Users cannot set them directly.

**Implemented:** `_created`, `_updated`, `_updated_date`, `_accessed`, `_accessed_date`, `_content_type`, `_source`

See [SYSTEM-TAGS.md](SYSTEM-TAGS.md) for complete reference.

## Python API

```python
kp.tag("doc:1", {"status": "reviewed"})      # Add/update tag
kp.tag("doc:1", {"obsolete": ""})            # Delete tag (empty string)
kp.list_items(tags={"project": "myapp"})      # Exact key=value match
kp.list_items(tag_keys=["project"])           # Any doc with 'project' tag
kp.list_tags()                               # All distinct tag keys
kp.list_tags("project")                      # All values for 'project'
```

See [PYTHON-API.md](PYTHON-API.md) for complete Python API reference.

## See Also

- [META-TAGS.md](META-TAGS.md) — Contextual queries (`.meta/*`)
- [PROMPTS.md](PROMPTS.md) — Prompts for summarization, analysis, and agent workflows
- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Auto-managed system tags
- [KEEP-LIST.md](KEEP-LIST.md) — List and filter by tags
- [KEEP-FIND.md](KEEP-FIND.md) — Search with tag filters
- [LANGCHAIN-INTEGRATION.md](LANGCHAIN-INTEGRATION.md) — LangChain/LangGraph namespace-to-tag mapping
- [REFERENCE.md](REFERENCE.md) — Quick reference index
