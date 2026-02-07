---
tags:
  category: system
  context: tag-description
---
# Tag: `topic` — Cross-Cutting Subject Area

The `topic` tag marks an item's subject area. Topics are persistent themes that span projects and sessions.

## Characteristics

- **Persistent**: Topics endure across projects. "auth" remains relevant whether you're working on `myapp` or `api-v2`.
- **Cross-cutting**: A topic connects knowledge from different contexts.
- **Naming**: Use short, lowercase, hyphenated names: `auth`, `testing`, `performance`, `code-review`.

## Relationship to `project`

| Tag | Scope | Lifetime | Example |
|-----|-------|----------|---------|
| `topic` | Cross-cutting subject area | Persistent | `auth`, `testing`, `performance` |
| `project` | Bounded work context | Finite | `myapp`, `api-v2` |

Use `topic` alone for knowledge that transcends any single project. Use both together for knowledge that's specific to a project but also relevant to a broader subject.

## Examples

```bash
# Cross-project knowledge (topic only)
keep put "Token refresh needs clock sync within 30s" -t topic=auth -t type=learning

# Project-specific, but topically relevant
keep put "myapp uses PKCE for OAuth2" -t project=myapp -t topic=auth

# Search by topic across all projects
keep find "authentication" -t topic=auth

# List all items in a topic
keep list -t topic=auth

# Find recent work in a topic
keep find "auth" -t topic=auth --since P7D
```
