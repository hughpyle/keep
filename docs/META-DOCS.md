# Meta-Docs

Meta-docs are system documents that surface contextually relevant items when you view your current context (`keep now`) or retrieve any item (`keep get`). They answer the question: *what else should I be aware of right now?*

## How It Works

A meta-doc is a `.meta/*` system document containing **tag queries**. When you run `keep now` or `keep get`, each meta-doc's queries run against the store, and matching items appear in the output as named sections.

For example, when you run `keep now` while working on a project tagged `project=myapp`:

```yaml
---
id: now
tags: {project: myapp, topic: auth}
similar:
  - %a1b2 OAuth2 token refresh pattern
meta/todo:
  - %c3d4 validate redirect URIs
  - %e5f6 update auth docs for new flow
meta/learnings:
  - %g7h8 JSON validation before deploy saves hours
---
Working on auth flow refactor
```

The `meta/todo:` section appeared because you previously captured commitments tagged with `project=myapp`:

```bash
keep put "validate redirect URIs" -t act=commitment -t status=open -t project=myapp
```

The `meta/learnings:` section appeared because you previously captured a learning tagged with the same project:

```bash
keep put "JSON validation before deploy saves hours" -t type=learning -t project=myapp
```

## Bundled Meta-Docs

keep ships with two meta-docs:

### `.meta/todo` — Open Loops

Surfaces unresolved commitments, requests, offers, and blocked work. These are things someone said they'd do, or asked for, that aren't resolved yet.

**Queries:**
- `act=commitment status=open`
- `act=request status=open`
- `act=offer status=open`
- `status=blocked`

**Context keys:** `project=`, `topic=`

### `.meta/learnings` — Experiential Priming

Surfaces past learnings, breakdowns, and gotchas. Before starting work, check what went wrong or was hard-won last time.

**Queries:**
- `type=learning`
- `type=breakdown`
- `type=gotcha`

**Context keys:** `project=`, `topic=`

## Query Structure

A meta-doc file contains prose (for humans and LLMs) plus structured lines:

- **Query lines** like `act=commitment status=open` — each `key=value` pair is an AND filter; multiple query lines are OR'd together
- **Context-match lines** like `project=` — a bare key whose value is filled from the current item's tags

Context matching is what makes meta-docs contextual. If the current item has `project=myapp`, then the query `act=commitment status=open` combined with context key `project=` becomes `act=commitment status=open project=myapp`. This scopes results to the current project.

If the current item has no matching tag for a context key, the query runs without that filter.

## Ranking

Results are ranked by a combination of:

1. **Embedding similarity** to the current item — so semantically related items rank higher
2. **Recency decay** — recent items get a boost

Each meta-doc returns up to 3 items. Meta sections with no matches are omitted from the output.

## Viewing Meta-Docs

You can read the meta-doc definitions themselves:

```bash
keep get .meta/todo        # See the todo meta-doc's queries and description
keep get .meta/learnings   # See the learnings meta-doc's queries
```

## Feeding the Loop

Meta-docs only surface what you put in. The tags that matter:

```bash
# Commitments and requests (surface in meta/todo)
keep put "I'll fix the login bug" -t act=commitment -t status=open -t project=myapp
keep put "Can you review the PR?" -t act=request -t status=open -t project=myapp

# Resolve when done
keep put "Login bug fixed" -t act=commitment -t status=fulfilled -t project=myapp

# Learnings and breakdowns (surface in meta/learnings)
keep put "Always check token expiry before refresh" -t type=learning -t topic=auth
keep put "Assumed UTC, server was local time" -t type=breakdown -t project=myapp

# Gotchas (surface in meta/learnings)
keep put "CI cache invalidation needs manual clear after dep change" -t type=gotcha -t topic=ci
```

The tag taxonomy is documented in system docs:

```bash
keep get .tag/act       # Speech-act categories
keep get .tag/status    # Lifecycle status values
keep get .tag/type      # Content type values
keep get .tag/project   # Project tag conventions
keep get .tag/topic     # Topic tag conventions
```

## See Also

- [SKILL.md](../SKILL.md) — The reflective practice
- [REFERENCE.md](REFERENCE.md) — Complete CLI reference
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
