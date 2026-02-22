---
tags:
  category: system
  context: prompt
---
# .prompt/summarize/default

Default summarization system prompt for general documents.

## Injection

This is a prompt doc — the `## Prompt` section below replaces the default summarization system prompt sent to the LLM. This doc has no match rules, so it acts as the fallback when no more-specific prompt doc matches.

To create a specialized prompt, add a new `.prompt/summarize/NAME` doc with match rules (e.g., `topic=code`) before the `## Prompt` section. The most specific match wins.

## Prompt

Summarize this document in under 200 words.

Begin with the subject or topic directly - do not start with meta-phrases like "This document describes..." or "The main purpose is...".

Good: Start with the name of the subject, then say what it is.
Bad: "This document describes..." or "The main purpose is..."

Include what it does, key features, and why someone might find it useful.
