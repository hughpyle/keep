---
tags:
  category: system
  context: prompt
---
# .prompt/agent/query

Answer questions using retrieved memory context.

## Prompt

Answer the following question using the retrieved context.
Investigate further as needed — retrieve specific items by ID,
perform additional searches, or examine version history to
build a complete answer.

Context:
{find:8000}

Question: {text}

Answer based on what's available. Make reasonable inferences from the context,
but clearly mark any uncertainty. If the context is insufficient, say so.
