---
tags:
  category: system
  context: tag-description
---
# Tag: `project` — Bounded Work Context

The `project` tag marks an item as belonging to a specific, bounded piece of work. Projects have a beginning, an end, and a scope.

## Characteristics

- **Temporal**: Projects start and finish. "myapp" is a project; "authentication" is a topic.
- **Bounded**: A project has a defined scope of work.
- **Naming**: Use short, lowercase, hyphenated names: `myapp`, `api-v2`, `q1-migration`.

## Relationship to `topic`

| Tag | Scope | Lifetime | Example |
|-----|-------|----------|---------|
| `project` | Bounded work context | Finite | `myapp`, `api-v2` |
| `topic` | Cross-cutting subject area | Persistent | `auth`, `testing`, `performance` |

An item often has both: `-t project=myapp -t topic=auth` means "authentication work within the myapp project." Knowledge tagged with `topic` only persists across projects.

## Injection

When `analyze --tags project` is used, the full text of this doc is prepended to the analysis prompt as guide context. This tag is unconstrained — values are free-form.

## Prompt

Identify the bounded work context this belongs to. Projects have a beginning, end, and scope. Use short, lowercase, hyphenated names (e.g., `myapp`, `api-v2`, `q1-migration`).

## Examples

```bash
# Project-specific work
keep put "OAuth2 with PKCE chosen for auth" -t project=myapp -t topic=auth

# Search within a project
keep find "authentication" -t project=myapp

# List everything in a project
keep list -t project=myapp

# Cross-project search by topic
keep find "authentication" -t topic=auth
```
