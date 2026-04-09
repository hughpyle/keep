---
tags:
  category: system
  context: tag-description
  _inverse: topic_of
---
# Tag: `topic` — Cross-Cutting Subject Area

The `topic` tag links an item to a persistent concept node — a subject area that spans projects and sessions. It is an **edge tag**: each value is a reference to a topic node (a `keep` item whose id is the topic name), and the inverse `topic_of` back-edge is maintained automatically.

The `_inverse: topic_of` declaration means this tag creates a navigable relationship edge. If item A has `topic: machine-learning`, then `get .topic/machine-learning` will show A under `topic_of:`.

## Characteristics

- **Edge-creating**: The `_inverse: topic_of` declaration makes this an edge tag. Topic nodes live at `.topic/<name>` and are auto-vivified on first use.
- **Persistent**: Topics endure across projects. `auth` remains relevant whether you're working on `myapp` or `api-v2`.
- **Cross-cutting**: A topic connects knowledge from different contexts — the edge is the connection.
- **Multi-valued**: An item can belong to multiple topics.
- **Naming**: Use short, lowercase, hyphenated names: `auth`, `testing`, `performance`, `machine-learning`.

## Relationship to `project`

| Tag | Scope | Lifetime | Example |
|-----|-------|----------|---------|
| `topic` | Cross-cutting subject area | Persistent | `auth`, `testing`, `performance` |
| `project` | Bounded work context | Finite | `myapp`, `api-v2` |

Use `topic` alone for knowledge that transcends any single project. Use both together for knowledge specific to a project but also relevant to a broader subject.

## Navigation

Because `topic` is an edge tag, you can navigate the concept graph in both directions:

```bash
# What does this item touch?
keep get my-note
# → topic:
# →   - machine-learning  [2026-04-08] Concept: machine learning, statistical ...
# →   - keep-workflow      [2026-04-08] Concept: keep workflow patterns

# What items are about this topic?
keep get .topic/machine-learning
# → topic_of:
# →   - my-note         [2026-04-08] Notes on UMAP dimensionality reduction
# →   - some-paper      [2026-04-07] Survey of temporal knowledge graph methods
# →   - a-session       [2026-04-06] Session discussing embedding approaches
```

## Injection

When `analyze --tags topic` is used, the full text of this doc is prepended to the analysis prompt as guide context for decomposition. This tag is unconstrained — values are free-form topic names, not validated against sub-docs.

## Prompt

Identify the subject area or theme. Use short, lowercase, hyphenated names. A topic is a persistent theme that spans projects (e.g., `auth`, `testing`, `performance`, `machine-learning`, `devops`). Prefer existing topics over creating new ones. Each value becomes an edge to a concept node — only assign topics that genuinely describe the item's subject matter.

## Examples

```bash
# Cross-project knowledge (topic only)
keep put "Token refresh needs clock sync within 30s" -t topic=auth -t type=learning

# Project-specific, but topically relevant
keep put "myapp uses PKCE for OAuth2" -t project=myapp -t topic=auth

# Item with multiple topics
keep put "UMAP reduces embedding space for retrieval" -t topic=machine-learning -t topic=keep-workflow

# Navigate: all items about a topic
keep get .topic/machine-learning

# Navigate: what topics does this item touch?
keep get my-note

# Search by topic across all projects
keep find "authentication" -t topic=auth

# List all items in a topic
keep list -t topic=auth
```
