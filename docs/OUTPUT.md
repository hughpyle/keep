# Reading the Output

When you run `keep get` or `keep now`, items are displayed in YAML frontmatter format. Each section carries meaning.

## Annotated Example

```yaml
---
id: %a1b2c3d4                                            # 1. Identity
tags: {project: myapp, topic: auth, act: commitment, status: open}  # 2. Tags
score: 0.823                                              # 3. Relevance
similar:                                                  # 4. Similar items
  - %e5f6a7b8         (0.89) 2026-01-14 OAuth2 token refresh pattern...
  - %c9d0e1f2         (0.85) 2026-01-13 Token handling notes...
  - .tag/act           (0.45) 2026-01-10 Speech-act categories...
meta/todo:                                                # 5. Meta sections
  - %d3e4f5a6 validate redirect URIs
  - %b7c8d9e0 update auth docs for new flow
meta/learnings:
  - %f1a2b3c4 Token refresh needs clock sync
prev:                                                     # 6. Version navigation
  - @V{1} 2026-01-14 Previous version of this item...
  - @V{2} 2026-01-13 Older version...
---
I'll fix the auth bug by Friday                           # 7. Content
```

## Sections

### 1. `id:` — Identity

The document's unique identifier.

| Format | Meaning |
|--------|---------|
| `%a1b2c3d4` | Content-addressed (text mode, hash of content) |
| `file:///path/to/doc` | Local file URI |
| `https://example.com` | Web URL |
| `now` | The nowdoc (current intentions) |
| `.conversations` | System document (dotted prefix) |
| `.tag/act` | Tag description document |

When viewing an old version, a suffix appears: `id: %a1b2c3d4@V{1}`

### 2. `tags:` — Metadata

Key-value pairs in YAML flow format. One value per key.

```yaml
tags: {project: myapp, topic: auth, act: commitment, status: open}
```

Tags you set are shown here. System tags (prefixed `_`) are hidden from display but accessible via `--json`.

Key tag patterns:
- **`project`** / **`topic`** — organize by bounded work vs cross-cutting subject. See [TAGGING.md](TAGGING.md#organizing-by-project-and-topic).
- **`act`** / **`status`** — speech-act tracking (commitment, request, assertion + lifecycle). See [TAGGING.md](TAGGING.md#speech-act-tags).
- **`type`** — content classification (learning, breakdown, reference, teaching)

### 3. `score:` — Relevance

Appears on search results (`keep find`). A similarity score between 0 and 1, with recency decay applied.

Higher = more relevant. Scores above 0.7 are strong matches. This field is absent when viewing items directly (not via search).

### 4. `similar:` — Semantic neighbors

Items semantically close to this one, ranked by similarity. Each line:

```
  - ID               (score) date summary...
```

- **ID** — the similar item's identifier (use with `keep get`)
- **(score)** — cosine similarity (0–1)
- **date** — last updated date
- **summary** — truncated summary

Similar items are occasions for reflection: what else do I know about this? Control with `-n` (limit) or `--no-similar` to suppress.

### 5. `meta/*:` — Contextual sections

Meta-docs surface items matching tag-based queries relevant to what you're viewing. Each section name maps to a meta-doc:

| Section | Surfaces | Source |
|---------|----------|--------|
| `meta/todo:` | Open requests and commitments | `.meta/todo` |
| `meta/learnings:` | Relevant learnings | `.meta/learnings` |
| `meta/decisions:` | Related decisions | `.meta/decisions` |

These are dynamically resolved — the same item shows different meta sections depending on its tags. For example, an item tagged `project=myapp` surfaces todos and learnings also tagged with that project.

See [META-DOCS.md](META-DOCS.md) for how meta-docs work and how to create custom ones.

### 6. `prev:` / `next:` — Version navigation

Navigate through the item's version history.

```yaml
prev:
  - @V{1} 2026-01-14 Previous version summary...
  - @V{2} 2026-01-13 Older version summary...
```

- **`@V{N}`** — version offset (1=previous, 2=two ago). Use with `keep get "ID@V{1}"` or `keep get ID -V 1`.
- Only appears on single-item display (`keep get`, `keep now`).
- `next:` appears when viewing an old version, pointing toward newer versions.

See [VERSIONING.md](VERSIONING.md) for full versioning details.

### 7. Content (after `---`)

The item's summary, below the closing `---`. For short content this is the full text; for long documents it's a generated summary.

## Output Formats

The frontmatter format is one of four output modes:

| Flag | Format | Use case |
|------|--------|----------|
| *(default)* | Summary line or frontmatter | Frontmatter for single items, summary lines for lists |
| `--ids` | Versioned IDs only | Piping to other commands |
| `--full` | YAML frontmatter (forced) | When you want full details on list/find results |
| `--json` | JSON | Programmatic access |

See [REFERENCE.md](REFERENCE.md#output-formats) for examples of each format.

## See Also

- [TAGGING.md](TAGGING.md) — Tag system and conventions
- [VERSIONING.md](VERSIONING.md) — Version history and navigation
- [META-DOCS.md](META-DOCS.md) — How meta sections are resolved
- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Hidden system tags
- [REFERENCE.md](REFERENCE.md) — Quick reference index
