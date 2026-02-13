# Reflective Memory — Agent Guide

Patterns for using the reflective memory store effectively in working sessions.

For the practice (why and when), see [../SKILL.md](../SKILL.md).
For CLI reference, see [REFERENCE.md](REFERENCE.md). Be sure you understand the [output format](OUTPUT.md) — every item surfaces similar items and meta sections you can navigate with `keep get`.

---

## The Practice

This guide assumes familiarity with the reflective practice in [SKILL.md](../SKILL.md). The key points:

**Reflect before acting:** Check your current work context and intentions.
- What kind of conversation is this? (Action? Possibility? Clarification?)
- What do I already know?
```bash
keep now                    # Current intentions
keep find "this situation"  # Prior knowledge
```

**While acting:** Is this leading to harm? If yes: give it up.

**Reflect after acting:** What happened? What did I learn?
```bash
keep put "what I learned" -t type=learning
```

**Periodically:** Run a full structured reflection:
```bash
keep reflect
```

This cycle — reflect, act, reflect — is the mirror teaching. Memory isn't storage; it's how you develop skillful judgment.

---

## Working Session Pattern

Use the nowdoc as a scratchpad to track where you are in the work. This isn't enforced structure — it's a convention that helps you (and future agents) maintain perspective.

```bash
# 1. Starting work — check context and intentions
keep now                                    # What am I working on?

# 2. Update context as work evolves (tag by project and topic)
keep now "Diagnosing flaky test in auth module" -t project=myapp -t topic=testing
keep now "Found timing issue" -t project=myapp

# 3. Check previous context if needed
keep now -V 1                               # Previous version
keep now --history                          # List all versions
keep now -t project=myapp                   # Find recent now with project tag

# 4. Record learnings (cross-project knowledge uses topic only)
keep put "Flaky timing fix: mock time instead of real assertions" -t topic=testing -t type=learning
```

**Key insight:** The store remembers across sessions; working memory doesn't. When you resume, read context first. All updates create version history automatically.

---

## Agent Handoff

**Starting a session:**
```bash
keep now                              # Current intentions with version history
keep now --history                    # How intentions evolved
keep find "recent work" --since P1D   # Last 24 hours
```

**Ending a session:**
```bash
keep now "Completed OAuth2 flow. Token refresh working. Next: add tests." -t topic=auth
keep move "auth-string" -t project=myapp  # Archive this string of work
```

---

## Strings

As you work, `keep now` accumulates a string of versions — a trace of how intentions evolved. `keep move` lets you name and archive that string, making room for what's next. It requires `-t` (tag filter) or `--only` (tip only) to prevent accidental grab-all moves.

**Snapshot before pivoting.** When the conversation shifts topic, move what you have so far before moving on:
```bash
keep move "auth-string" -t project=myapp     # Archive the auth string
keep now "Starting on database migration"    # Fresh context for new work
```

**Incremental archival.** Move to the same name repeatedly — versions append, building a running log across sessions:
```bash
# Session 1
keep move "design-log" -t project=myapp
# Session 2 (more work on same project)
keep move "design-log" -t project=myapp      # Appends new versions
```

**End-of-session archive.** When a string of work is complete:
```bash
keep move "auth-string" -t project=myapp
```

**Tag-filtered extraction.** When a session mixes multiple projects, extract just the string you want:
```bash
keep move "frontend-work" -t project=frontend   # Leaves backend versions in now
```

The moved item is a full versioned document — browse with `keep get name --history`, navigate with `-V 1`, `-V 2`, etc.

---

## Index Important Documents

Whenever you encounter documents important to the task, index them:

```bash
keep put "https://docs.example.com/auth" -t topic=auth -t project=myapp
keep put "file:///path/to/design.pdf" -t type=reference -t topic=architecture
```

Ask: what is this? Why is it important? Tag appropriately. Documents indexed during work become navigable knowledge.

---

## Breakdowns as Learning

When the normal flow is interrupted — expected response doesn't come, ambiguity surfaces — an assumption has been revealed. **First:** complete the immediate conversation. **Then record:**

```bash
keep put "Assumed user wanted full rewrite. Actually: minimal patch." -t type=breakdown
```

Breakdowns are how agents learn.

---

## Tracking Commitments

Use speech-act tags to make the commitment structure of work visible:

```bash
# Track promises
keep put "I'll fix the auth bug" -t act=commitment -t status=open -t project=myapp

# Track requests
keep put "Please review the PR" -t act=request -t status=open

# Query open work
keep list -t act=commitment -t status=open

# Close the loop
keep tag-update ID --tag status=fulfilled
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

Bundled system docs provide patterns and conventions, accessible via `keep get`:

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
