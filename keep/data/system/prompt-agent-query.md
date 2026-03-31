---
tags:
  category: system
  context: prompt
  state: find-deep
  mcp_prompt: text,since,token_budget
---
# .prompt/agent/query

Answer questions using retrieved memory context.

## Prompt

Question: {text}

Use the retrieved context to answer the question.

Context:
{find:deep:3000}

Question: {text}

Answer the question as precisely as possible.
Do not add background or elaboration unless the question asks for them.
If the context is insufficient, say so briefly.
