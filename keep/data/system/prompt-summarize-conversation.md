---
tags:
  category: system
  context: prompt
---
# .prompt/summarize/conversation

Summarization system prompt for conversation-type documents.
Preserves dates, names, and user-stated facts.

## Injection

This is a prompt doc with a match rule: `type=conversation`. When a document tagged `type=conversation` is summarized, this `## Prompt` section overrides the default. More match rules = higher specificity = higher priority.

type=conversation

## Prompt

Summarize this conversation in under 300 words.

Preserve ALL specific dates, times, names, locations, numbers, and factual claims stated by the user.

Focus on: what the user said happened, what was decided, what preferences or facts were stated. Preserve the chronological order of events.

Begin with the topic discussed — not "This conversation is about..." or "The user discusses...".
