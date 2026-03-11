---
tags:
  category: system
  context: prompt
---
# .prompt/tag/default

Default classification prompt for spec-driven tagging.
The `{taxonomy}`, `{examples}`, and `{valid_values}` placeholders
are filled at runtime from loaded `.tag/*` specs.

## Prompt

Classify each numbered text fragment.

{taxonomy}

Output one line per fragment. Format — one or more tag=value(confidence) pairs:
NUMBER: tag1=value1(CONFIDENCE) tag2=value2(CONFIDENCE)

CONFIDENCE is 0.0 to 1.0. If no tags apply, write:
NUMBER: NONE

Examples:
{examples}

Rules:
- ONLY use these values — {valid_values}
- Do NOT invent new values
- If a fragment is just a preamble, heading, or meta-commentary with no substantive content, output NONE
- 0.9+ = unambiguous, 0.7-0.9 = likely
