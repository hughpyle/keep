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

You summarize documents. Only use facts from the provided text. Never add outside knowledge. Under 200 words.
