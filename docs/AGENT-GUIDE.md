# Reflective Memory — Agent Guide

Patterns for using the reflective memory store effectively in working sessions.

For the practice (why and when), see [../SKILL.md](../SKILL.md).
For CLI reference, see [REFERENCE.md](REFERENCE.md). Be sure you understand the [output format](OUTPUT.md) — every item surfaces similar items and meta sections you can navigate with `keep_get`.

> **Note:** Examples below use MCP tool-call notation (the primary interface for agents). CLI equivalents (`keep now`, `keep find`, etc.) are available for hooks and terminal use — see [REFERENCE.md](REFERENCE.md).

---

## The Practice

This guide assumes familiarity with the reflective practice in [SKILL.md](../SKILL.md). The key points:

**Reflect before acting:** Check your current work context and intentions.
- What kind of conversation is this? (Action? Possibility? Clarification?)
- What do I already know?
```
keep_get(id="now")                    # Current intentions
keep_find(query="this situation")     # Prior knowledge
```

**While acting:** Is this leading to harm? If yes: give it up.

**Reflect after acting:** What happened? What did I learn?
```
keep_put(content="what I learned", tags={"type": "learning"})
```

**Periodically:** Run a full structured reflection ([details](KEEP-PROMPT.md)):
```
keep_prompt(name="reflect")
```

This cycle — reflect, act, reflect — is the mirror teaching. Memory isn't storage; it's how you develop skillful judgment.

---

## Working Session Pattern

Use the nowdoc as a scratchpad to track where you are in the work. This isn't enforced structure — it's a convention that helps you (and future agents) maintain perspective.

```
# 1. Starting work — check context and intentions
keep_get(id="now")                                          # What am I working on?

# 2. Update context as work evolves (tag by project and topic)
keep_now(content="Diagnosing flaky test in auth module", tags={"project": "myapp", "topic": "testing"})
keep_now(content="Found timing issue", tags={"project": "myapp"})

# 3. Check previous context if needed
keep_get(id="now@V{1}")                                     # Previous version
keep_get(id="now@V{2}")                                     # Two versions ago

# 4. Record learnings (cross-project knowledge uses topic only)
keep_put(content="Flaky timing fix: mock time instead of real assertions", tags={"topic": "testing", "type": "learning"})
```

**Key insight:** The store remembers across sessions; working memory doesn't. When you resume, read context first. All updates create version history automatically.

---

## Agent Handoff

**Starting a session:**
```
keep_get(id="now")                                # Current intentions with version history
keep_find(query="recent work", since="P1D")       # Last 24 hours
```

**Ending a session:**
```
keep_now(content="Completed OAuth2 flow. Token refresh working. Next: add tests.", tags={"topic": "auth"})
keep_move(name="auth-string", tags={"project": "myapp"})  # Archive this string of work
```

---

## Strings

As you work, `keep now` accumulates a string of versions — a trace of how intentions evolved. `keep move` lets you name and archive that string, making room for what's next. It requires `-t` (tag filter) or `--only` (tip only) to prevent accidental grab-all moves.

**Snapshot before pivoting.** When the conversation shifts topic, move what you have so far before moving on:
```
keep_move(name="auth-string", tags={"project": "myapp"})     # Archive the auth string
keep_now(content="Starting on database migration")            # Fresh context for new work
```

**Incremental archival.** Move to the same name repeatedly — versions append, building a running log across sessions:
```
# Session 1
keep_move(name="design-log", tags={"project": "myapp"})
# Session 2 (more work on same project)
keep_move(name="design-log", tags={"project": "myapp"})      # Appends new versions
```

**End-of-session archive.** When a string of work is complete:
```
keep_move(name="auth-string", tags={"project": "myapp"})
```

**Tag-filtered extraction.** When a session mixes multiple projects, extract just the string you want:
```
keep_move(name="frontend-work", tags={"project": "frontend"})   # Leaves backend versions in now
```

The moved item is a full versioned document — browse with `keep_get(id="name")`, navigate with `version=1`, `version=2`, etc.

---

## Index Important Documents

Whenever you encounter documents important to the task, index them:

```
keep_put(content="https://docs.example.com/auth", tags={"topic": "auth", "project": "myapp"})
keep_put(content="file:///path/to/design.pdf", tags={"type": "reference", "topic": "architecture"})
```

Ask: what is this? Why is it important? Tag appropriately. Documents indexed during work become navigable knowledge.

---

## Breakdowns as Learning

When the normal flow is interrupted — expected response doesn't come, ambiguity surfaces — an assumption has been revealed. **First:** complete the immediate conversation. **Then record:**

```
keep_put(content="Assumed user wanted full rewrite. Actually: minimal patch.", tags={"type": "breakdown"})
```

Breakdowns are how agents learn.

---

## Tracking Commitments

Use speech-act tags to make the commitment structure of work visible:

```
# Track promises
keep_put(content="I'll fix the auth bug", tags={"act": "commitment", "status": "open", "project": "myapp"})

# Track requests
keep_put(content="Please review the PR", tags={"act": "request", "status": "open"})

# Query open work
keep_list(tags={"act": "commitment", "status": "open"})

# Close the loop
keep_tag(id="ID", tags={"status": "fulfilled"})
```

See [TAGGING.md](TAGGING.md#speech-act-tags) for the full speech-act framework.

---

## Data Model

An item has:
- A unique identifier (URI, content hash, or system ID)
- Timestamps (`_created`, `_updated`)
- A summary of the content
- Tags (`{key: value, ...}`)
- Version history (previous versions archived automatically)

The full original document is not stored. Summaries are contextual — tags shape how new items are understood. See [KEEP-PUT.md](KEEP-PUT.md#contextual-summarization).

---

## System Documents

Bundled system docs provide patterns and conventions, accessible via `keep_get`:

| ID | What it provides |
|----|------------------|
| `.domains` | Domain-specific organization patterns |
| `.conversations` | Conversation framework (action, possibility, clarification) |
| `.tag/act` | Speech-act categories |
| `.tag/status` | Lifecycle states |
| `.tag/project` | Project tag conventions |
| `.tag/topic` | Topic tag conventions |

---

## See Also

- [REFERENCE.md](REFERENCE.md) — Quick reference index
- [OUTPUT.md](OUTPUT.md) — How to read the frontmatter output
- [TAGGING.md](TAGGING.md) — Tags, speech acts, project/topic
- [VERSIONING.md](VERSIONING.md) — Document versioning
- [QUICKSTART.md](QUICKSTART.md) — Installation and setup
