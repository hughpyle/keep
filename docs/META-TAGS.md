# Meta-Tags

Meta-tags are system documents stored at `.tag/*`, `.meta/*`, and `.prompt/*` that define your store's tag vocabulary, contextual queries, and LLM prompt overrides. They serve three purposes:

1. **Tag descriptions** (`.tag/*`) — Define tags, constrain valid values, and guide auto-tagging during analysis
2. **Contextual queries** (`.meta/*`) — Surface relevant items when you view context with `keep now` or `keep get`
3. **Prompt overrides** (`.prompt/*`) — Customize the LLM prompts used for summarization and analysis

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

### Bundled tag descriptions

keep ships with these tag descriptions:

| Tag | Constrained | Values | Purpose |
|-----|:-----------:|--------|---------|
| `act` | Yes | `commitment`, `request`, `offer`, `assertion`, `assessment`, `declaration` | Speech-act category (what the speaker is doing) |
| `status` | Yes | `open`, `blocked`, `fulfilled`, `declined`, `withdrawn`, `renegotiated` | Lifecycle state of commitments/requests/offers |
| `type` | No | `learning`, `breakdown`, `gotcha`, `reference`, `teaching`, `meeting`, `pattern`, `possibility`, `decision` | Content classification |
| `project` | No | (user-defined) | Bounded work context |
| `topic` | No | (user-defined) | Cross-cutting subject area |

Constrained tags (`act`, `status`) also have individual sub-documents (e.g., `.tag/act/commitment`, `.tag/status/open`) that describe each value in detail.

Unconstrained tags (`type`, `project`, `topic`) accept any value. Their descriptions document conventions but don't enforce them.

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

## Contextual queries (`.meta/*`)

Meta-tags at `.meta/*` contain **tag queries** that surface relevant items when you run `keep now` or `keep get`. They answer: *what else should I be aware of right now?*

Meta docs currently serve as query patterns — they define what gets surfaced as context, not how the LLM behaves.

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

### Bundled contextual queries

keep ships with five `.meta/*` documents:

#### `.meta/todo` — Open Loops

Surfaces unresolved commitments, requests, offers, and blocked work.

**Queries:** `act=commitment status=open`, `act=request status=open`, `act=offer status=open`, `status=blocked`
**Context keys:** `project=`, `topic=`

#### `.meta/learnings` — Experiential Priming

Surfaces past learnings, breakdowns, and gotchas before you start work.

**Queries:** `type=learning`, `type=breakdown`, `type=gotcha`
**Context keys:** `project=`, `topic=`

#### `.meta/genre` — Same Genre

Groups media items by genre. Only activates for items with a `genre` tag.

**Prerequisites:** `genre=*`
**Context keys:** `genre=`

#### `.meta/artist` — Same Artist

Groups media items by artist. Only activates for items with an `artist` tag.

**Prerequisites:** `artist=*`
**Context keys:** `artist=`

#### `.meta/album` — Same Album

Groups tracks from the same release. Only activates for items with an `album` tag.

**Prerequisites:** `album=*`
**Context keys:** `album=`

### Query structure

A `.meta/*` document contains prose (for humans and LLMs) plus structured lines:

- **Query lines** like `act=commitment status=open` — each `key=value` pair is an AND filter; multiple query lines are OR'd together
- **Context-match lines** like `project=` — a bare key whose value is filled from the current item's tags
- **Prerequisite lines** like `genre=*` — the current item must have this tag or the entire query is skipped

Context matching is what makes these queries contextual. If the current item has `project=myapp`, then `act=commitment status=open` combined with context key `project=` becomes `act=commitment status=open project=myapp` — scoped to the current project.

Prerequisites act as gates. A query with `genre=*` only activates for items that have a `genre` tag — items without one skip it entirely.

### Ranking

Results are ranked by:

1. **Embedding similarity** to the current item — semantically related items rank higher
2. **Recency decay** — recent items get a boost

Each contextual query returns up to 3 items. Sections with no matches are omitted.

### Viewing definitions

```bash
keep get .meta/todo        # See the todo query definition
keep get .meta/learnings   # See the learnings query definition
keep list .meta            # All contextual query definitions
```

## Prompt overrides (`.prompt/*`)

Prompt docs at `.prompt/summarize/*` and `.prompt/analyze/*` let you customize the LLM system prompts used for summarization and analysis. Unlike tag docs (which augment the prompt), prompt docs **replace** the default system prompt entirely.

### How prompt docs work

Each prompt doc has two parts:

1. **Match rules** — tag queries that determine when this prompt applies (same DSL as `.meta/*` docs)
2. **`## Prompt` section** — the actual system prompt text sent to the LLM

When a document is summarized or analyzed, keep scans all `.prompt/{type}/*` docs, finds those whose match rules match the document's tags, and selects the most specific match (most rules matched). The `## Prompt` section from the winner replaces the default system prompt.

### Bundled prompt docs

| ID | Match rule | Purpose |
|----|-----------|---------|
| `.prompt/summarize/default` | *(none — fallback)* | Default summarization prompt |
| `.prompt/summarize/conversation` | `type=conversation` | Preserves dates, names, facts from conversations |
| `.prompt/analyze/default` | *(none — fallback)* | Default analysis prompt for structural decomposition |
| `.prompt/analyze/conversation` | `type=conversation` | Fact extraction from conversations |

### Creating custom prompts

Create a new prompt doc with match rules targeting specific tags:

```bash
# Custom summarization for code documentation
keep put "$(cat <<'EOF'
topic=code

## Prompt

Summarize this code documentation in under 200 words.
Focus on: what the API does, key parameters, return values, and common pitfalls.
Begin with the function or class name.
EOF
)" --id .prompt/summarize/code
```

Match rules can combine multiple tags for higher specificity:

```bash
# Prompt for meeting notes in a specific project
keep put "$(cat <<'EOF'
type=meeting project=myapp

## Prompt

Summarize this meeting in under 300 words.
Focus on decisions made, action items assigned, and deadlines mentioned.
List each action item with its owner.
EOF
)" --id .prompt/summarize/myapp-meetings
```

The most specific match wins — a prompt matching `type=meeting project=myapp` (2 rules) beats one matching just `type=meeting` (1 rule), which beats the default (0 rules).

### Viewing prompt docs

```bash
keep get .prompt/summarize/default      # See the default summarization prompt
keep get .prompt/analyze/conversation   # See the conversation analysis prompt
keep list .prompt                       # All prompt docs
```

## Feeding the loop

Meta-tags only surface what you put in. The tags that matter:

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

### Media library

The media queries (`genre`, `artist`, `album`) surface related media automatically:

```bash
keep put ~/Music/OK_Computer/01_Airbag.flac -t artist=Radiohead -t album="OK Computer" -t genre=rock
```

Now `keep get` on that item shows `meta/artist:`, `meta/album:`, and `meta/genre:` sections with related tracks.

## The `## Injection` section

Every system doc contains a `## Injection` section that describes how its content flows into the system. This serves as inline documentation — when you `keep get .tag/act`, the Injection section tells you exactly how the doc's content is used (guide context, classification, query pattern, or prompt override).

## See Also

- [EDGE-TAGS.md](EDGE-TAGS.md) — Edge tags: navigable relationships via `_inverse`
- [TAGGING.md](TAGGING.md) — Tag basics: setting, filtering, isolation
- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Auto-managed system tags (`_created`, `_updated`, etc.)
- [ANALYSIS.md](ANALYSIS.md) — How analysis uses tag descriptions to auto-tag parts
- [KEEP-ANALYZE.md](KEEP-ANALYZE.md) — CLI reference for `keep analyze`
- [REFERENCE.md](REFERENCE.md) — Complete CLI reference
