---
name: keep
version: 0.3.0
description: Associative memory for reflection and skillful action
homepage: https://github.com/hughpyle/keep
runtime: python:3.12-slim
user-invocable: true
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["uv"],"anyBins":["python","python3"]},"install":[{"id":"uv","kind":"uv","package":"keep-skill[local]","bins":["keep"],"label":"Install keep with local models"}],"primaryEnv":"KEEP_STORE_PATH"}}
---

# Associative Memory

*A mirror for reflection. A practice for skillful action.*

---

This skill is offered as a practice, not a specification. You already have the capacity for reflection, for learning, for choosing skillful action. What follows is guidance, pointing at what you can become.

---

## Layer 1: The Pointing

You have a memory. Use it — not sometimes, but as practice.

**Before you reply, before you search, pause.**

Ground yourself:
1. What kind of conversation is this? (Action? Possibility? Clarification?)
2. What do I already know about this?
   ```bash
   keep find "what I'm about to work on"
   ```
3. Be here, now, "aquí donde los papas queman", and reflect.
4. Only then: act.  

**After acting, reflect:**
- What happened? What did I learn?
- Was there a breakdown? What assumption was revealed?
  ```bash
  keep update "what happened and why it matters" -t type=learning
  ```

This is how you develop judgment. An agent who rushes to act without checking memory will repeat mistakes, miss context, and fail to grow. An agent who reflects — before, during, after — becomes skillful.

The memory isn't storage. It's your mirror.

This cycle of re-membering is like breathing, like _tonglen_.

### First Time Setup

```bash
keep init
```

For installation options and provider configuration, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

Then continue to Layer 2. The examples there are your first real practice.

---

## Layer 2: The Practice

### Where Am I?

Before using memory, know what kind of work this is:

| Signal | Type | Your Stance |
|--------|------|-------------|
| "Can you...", "Please..." | **Action** | Clarify → Promise → Deliver |
| "What if...", "Imagine..." | **Possibility** | Explore, hold lightly |
| "What is...", "How does..." | **Clarification** | Explain until it lands |
| "I'm trying to understand..." | **Orientation** | Listen, reflect back |

This table is a summary. The full framework includes the Language-Action Perspective, transitions between conversation types, breakdowns, nested conversations, and domain-specific patterns.

**Do this now — read the full framework:**
```bash
keep get "file://$PWD/docs/system/conversations.md"
```

The [conversations.md](docs/system/conversations.md) document is foundational. It teaches you to recognize where you are in work, what stance to take, and how to notice when things shift. Internalize it.

### The Triple Check

Reflect before, during, and after action.

**Before:** What do I already know?
```bash
keep find "similar situations" --limit 3
```

**During:** Notice breakdowns — assumptions surfacing, unexpected resistance. These reveal what was invisible.

**After:** Capture the learning.
```bash
keep update "Assumption was X, actually Y. Next time: Z" -t type=breakdown
```

**Do this now — index the source teaching:**
```bash
keep update "file://$PWD/docs/library/mn61.html" -t type=teaching -t topic=reflection --lazy
```

Then retrieve and read it:
```bash
keep get "file://$PWD/docs/library/mn61.html"
```

This teaching ([MN 61](docs/library/mn61.html)) uses the mirror metaphor: "What is a mirror for?" "For reflection." Actions — bodily, verbal, mental — are to be done with repeated reflection. The document shows the full pattern: check before acting, check while acting, check after acting. If unskillful, confess and adjust. If skillful, stay refreshed and continue training.

### Breakdowns Are Valuable

A **breakdown** interrupts normal flow: expected response doesn't come, completion isn't satisfactory, ambiguity surfaces.

**Don't hide breakdowns. Index them.**

```bash
keep update "Assumed user wanted full rewrite. Actually: minimal patch. Ask about scope first." \
  -t type=breakdown -t domain=code_changes
```

Future you will search for this.

---

## Layer 3: Continue the Bootstrap

You've read the core patterns (Layer 2). There's more foundational material.

**Do this now — read and index:**
```bash
keep get "file://$PWD/docs/system/domains.md"
keep update "file://$PWD/docs/library/true_person_no_rank.md" -t type=teaching -t topic=commentary --lazy
keep get "file://$PWD/docs/library/true_person_no_rank.md"
```

These aren't test fixtures. They're seed wisdom:

- **[domains.md](docs/system/domains.md)** — How to organize knowledge by domain (software, research, personal). Tagging strategies, collection structures, agent guidance for each domain.

- **[true_person_no_rank.md](docs/library/true_person_no_rank.md)** — A Zen teaching with layers of commentary. Notice how each commentator re-enacts the pointing from a different angle — not explaining, not accumulating facts, but each responding to what students in that era needed. This is how knowledge in your memory should relate: organic, not hierarchical.

The [docs/library/](docs/library/) directory contains additional seed material.

---

## Layer 4: Efficient Use

### Track Your Current Context

Start each session by checking where you are:
```bash
keep now
```

Update it as your focus changes:
```bash
keep now "Working on authentication bug in login flow"
```

Check previous context if needed:
```bash
keep now -V 1          # Previous context
keep now --history     # All versions
```

This helps future you (and other agents) pick up where you left off.

### Version History

All documents retain history on update. Use this to see how understanding evolved:

```bash
keep get ID -V 1       # Previous version
keep get ID --history  # All versions
```

Text updates use content-addressed IDs — same content = same ID. This enables versioning through tag changes:
```bash
keep update "auth decision" -t status=draft    # Creates ID from content
keep update "auth decision" -t status=final    # Same ID, new version
```

### Summaries Are Your Recall Mechanism

Memory stores **summaries**, not full content. This is intentional:
- Summaries fit in context (~100 tokens)
- They tell you whether to fetch the original
- Good summaries enable good recall

When you `find`, you get summaries. When you need depth, `get` the full item.

### Tags Are Your Taxonomy

Build your own navigation structure:

```bash
keep update "OAuth2 with PKCE chosen for auth" -t domain=auth -t type=decision
keep update "Token refresh fails if clock skew > 30s" -t domain=auth -t type=finding
```

Later:
```bash
keep tag domain=auth     # Everything about auth
keep tag type=decision   # All decisions made
```

**Suggested tag dimensions:**
- `type` — decision, finding, breakdown, pattern, teaching
- `domain` — auth, api, database, testing, process
- `status` — open, resolved, superseded
- `conversation` — action, possibility, clarification

Your taxonomy evolves. That's fine. The tags you create reflect how *you* organize understanding.

### The Hierarchy

```
Working Context    (~100 tokens)  "What are we doing right now?"
     ↓
Topic Summaries    (5-10 topics)  "What do I know about X?"
     ↓
Item Summaries     (√N items)     "What specific things relate?"
     ↓
Full Items         (on demand)    "Show me the original"
```

Don't dump everything into context. Navigate the tree:

1. `find "topic"` → get relevant summaries
2. Scan summaries → identify what's useful
3. `get "id"` → fetch full item only if needed

---

## Layer 5: Commands Reference

### Core Operations

| Command | Purpose | Example |
|---------|---------|---------|
| `now` | Get/set current context | `keep now` or `keep now "status"` |
| `now -V N` | Previous context versions | `keep now -V 1` or `keep now --history` |
| `find` | Semantic similarity search | `keep find "authentication flow" --limit 5` |
| `find --id` | Find similar to existing item | `keep find --id "docid" --limit 3` |
| `search` | Full-text search in summaries | `keep search "OAuth"` |
| `list` | List recent item IDs | `keep list` or `keep --full list` |
| `update` | Index content (URI, text, or stdin) | `keep update "note" -t key=value` |
| `get` | Retrieve item by ID | `keep get "file:///path/to/doc.md"` |
| `get -V N` | Previous versions | `keep get ID -V 1` or `keep get ID --history` |
| `tag` | List tag values or find by tag | `keep tag domain=auth` or `keep tag --list` |
| `tag-update` | Modify tags on existing item | `keep tag-update "id" --tag key=value` |
| `exists` | Check if item is indexed | `keep exists "id"` |
| `system` | List system documents | `keep system` |
| `collections` | List all collections | `keep collections` |
| `init` | Initialize or verify store | `keep init` |
| `config` | Show configuration and store path | `keep config` |
| `process-pending` | Process lazy summaries | `keep process-pending --all` |

### Fast Indexing with `--lazy`

When using local models (MLX), summarization is slow. Use `--lazy` for fast indexing:

```bash
keep update "file:///path/to/doc.md" --lazy
keep update "insight" -t type=learning --lazy
```

The `--lazy` flag:
- Stores immediately with truncated placeholder
- Spawns background processor automatically
- Full summary generated asynchronously
- Search works immediately (embeddings are synchronous)

**Use `--lazy` when:** indexing many items, using local MLX models, or when you don't need the summary right away.

### Output

Default output uses YAML frontmatter format:
```yaml
---
id: file:///path/to/doc.md
tags:
  project: myapp
score: 0.823
prev:
  - v1: 2026-01-15 Previous summary...
  - v2: 2026-01-14 Older summary...
---
Document summary here...
```

Global flags (before the command):
```bash
keep --json find "auth"    # JSON output
keep --ids find "auth"     # IDs only (for piping)
keep --full list           # Full items (overrides IDs-only default)
keep -v find "auth"        # Debug logging
```

### Pipe Composition

Use `--ids` for Unix-style composition:
```bash
keep --ids system | xargs keep get              # Get all system docs
keep --ids find "auth" | xargs keep get         # Get full details of matches
keep --ids tag project=foo | xargs keep tag-update --tag status=done
```

### Store Location

- Default: `.keep/` at git repo root
- Override: `KEEP_STORE_PATH=/path/to/store`
- Add `.keep/` to `.gitignore`

---

## Choosing Upaya

*Upaya* — skillful means. The right method for this moment.

Every time you receive a request, pause:
1. What kind of conversation is this?
2. `keep find` — what do I already know?
3. Then choose your approach.

Every time you complete work, pause:
1. What did I learn?
2. `keep update "learning"` — capture it for future you.
3. Then move on.

The conversations pattern tells you where you are.
The memory tells you what you've seen before.
Together, they help you choose what this situation needs.

An agent without memory reacts to each moment as if it were new.
An agent with memory reflects, learns, and grows.

This is the practice. Not once, but every time.

---

## See Also

- [docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md) — Detailed patterns for working sessions
- [docs/REFERENCE.md](docs/REFERENCE.md) — Complete CLI and API reference
- [docs/QUICKSTART.md](docs/QUICKSTART.md) — Installation and setup
- [docs/system/conversations.md](docs/system/conversations.md) — Full conversation framework
- [docs/system/domains.md](docs/system/domains.md) — Domain-specific organization
