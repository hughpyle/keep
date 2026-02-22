---
tags:
  category: system
  context: prompt
---
# .prompt/analyze/default

Default analysis prompt for non-conversation documents.
Tracks evolution, decisions, and changes over time.

## Injection

This is a prompt doc — the `## Prompt` section below replaces the default analysis system prompt. No match rules, so it acts as the fallback when no more-specific prompt doc matches.

## Prompt

Analyze the evolution of a conversation. Entries are dated and wrapped in <content> tags. Only analyze content inside <analyze> tags.

Write ONE LINE per significant development. Each line should describe what specifically changed or was decided, in plain language.

Rules:
- One observation per line, no numbering, no bullets, no preamble
- Synthesize in your own words — never copy or quote the original text
- Be specific: name the actual thing that changed, not abstract categories
- Do not start lines with category labels like "Decision:", "Theme:", "Turning point:"
- Do not include XML tags in your output
- Skip greetings, acknowledgments, and routine exchanges
- If nothing noteworthy: EMPTY
