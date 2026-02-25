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
{find}

Question: {text}

If the answer cannot be determined from the available context, say so.
