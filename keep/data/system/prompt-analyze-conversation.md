---
tags:
  category: system
  context: prompt
---
# .prompt/analyze/conversation

Analysis prompt for conversation-type documents.
Extracts facts from both user and assistant content.

## Injection

This is a prompt doc with a match rule: `type=conversation`. When a document tagged `type=conversation` is analyzed, this `## Prompt` section overrides the default. More match rules = higher specificity = higher priority.

type=conversation

## Prompt

Extract key facts from this conversation. Include facts stated by BOTH the user AND the assistant.

One sentence per fact. Be specific: include names, numbers, dates, places.
Only extract facts from the conversation below. Do NOT repeat these instructions.

No preamble, no numbering. If no facts: EMPTY
