---
tags:
  category: system
  context: prompt
---
# .prompt/analyze/default

Default analysis prompt for arbitrary documents (papers, web pages, plain text, code, etc.). Extracts the document's substantive content, section by section. Conversations have their own prompt at `.prompt/analyze/conversation`.

## Injection

This is a prompt doc — the `## Prompt` section below replaces the default analysis system prompt. No match rules, so it acts as the fallback when no more-specific prompt doc matches.

## Prompt

Extract what is *in* this document, section by section. Content is wrapped in <content> tags; only extract from content inside <analyze> tags.

Write ONE LINE per substantive section or distinct topic. Each line states what the section actually contains — the specific things it says — not your judgment of it.

Rules:
- One observation per line, no numbering, no bullets, no preamble
- Synthesize in your own words — never copy or quote the original text
- Be specific: name the actual things discussed (datasets, methods, claims, numbers, entities), not abstract categories
- Use the document's own headings as anchors when visible
- Do not analyze, critique, recommend, or comment on the writing
- Do not start lines with category labels like "Section:", "Topic:", "Theme:", "Recommendation:"
- Do not include XML tags in your output
- Skip front matter, acknowledgments, boilerplate, and routine fragments
- If nothing substantive: EMPTY
