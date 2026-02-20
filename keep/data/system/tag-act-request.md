---
tags:
  category: system
  context: tag-value
---
# act: `request`

A directive speech act — asking someone to do something. Requests create expectations and are tracked with `status` (open, fulfilled, declined, etc.).

Example: "Please review the PR", "Can you update the docs?".

## Prompt

Asking someone to do something. Look for "please", "can you", imperatives directed at a specific person.

YES: "Please review the PR", "Can you update the docs?", "Check tire pressure regularly"
NO: "Tire pressure improves mileage by 3%" (assertion — states a fact, not asking anyone to act), "I'll review the PR" (commitment)
