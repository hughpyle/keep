---
tags:
  category: system
  context: tag-value
---
# act: `commitment`

A commissive speech act — a promise or pledge to act. The speaker binds themselves to a future course of action. Commitments create obligations and are tracked with `status` (open, fulfilled, withdrawn, etc.).

Example: "I'll fix auth by Friday", "I will review the PR tomorrow".

## Prompt

The speaker binds themselves to future action. Look for "I will", "I'll", "I decided to", "I'm going to".

YES: "I'll fix auth by Friday", "I decided to use Tableau", "I'm going to review the PR"
NO: "You should fix auth" (request), "Auth is broken" (assertion), "I could fix auth" (offer)
