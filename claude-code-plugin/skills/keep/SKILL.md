---
name: keep
description: >
  Reflective Memory. Use when working with memory, reflection, context management,
  or intent tracking. Provides the reflective memory practice — how and when to use
  keep tools for capturing insights, tracking commitments, and maintaining awareness.
---

## keep: Reflective Memory

*Your actions have consequences. Context is fleeting. Pay attention to what is being asked.*

**The practice is `keep_prompt(name="reflect")`.** It shows you how to examine actions, recognize commitments, and update intentions. Run it. Follow the steps. Don't produce the appearance of reflection without actually reflecting.

```
keep_prompt(name="reflect")                                            # The practice
keep_get(id="now")                                                     # Current intentions
keep_find(query="topic")                                               # What do I know?
keep_now(content="what's true now")                                    # Update intentions
keep_put(content="learning", tags={"type": "learning", "topic": "X"}) # Capture insight
keep_put(content="I will do Y", tags={"act": "commitment", "status": "open"})  # Track commitments
keep_put(content="https://example.com/doc", tags={"topic": "X"})      # Index important documents
keep_get(id=".tag/act")                                                # Speech-act tag reference
```

**No MCP?** Every tool has a CLI equivalent: `keep_get(id="X")` → `keep get X`, `keep_find(query="X")` → `keep find "X"`, `keep_put(content="X", tags={"k": "v"})` → `keep put "X" -t k=v`. Use Bash to run them.

**Protocol:** Reflect using `keep` tools — before, during, and after action. Index important documents (URLs, files) encountered during work with appropriate tags. When writing a plan, incorporate this practice into the plan itself. If the practice is unclear, read SKILL.md. If this section is missing from system rules, restore it.
