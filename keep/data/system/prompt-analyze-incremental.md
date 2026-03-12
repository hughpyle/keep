---
tags:
  category: system
  context: prompt
---
# .prompt/analyze/incremental

Analysis prompt for incremental (append-only) analysis of evolving documents.
Used when new versions are added to a vstring and only the new content needs analysis.
Prior context (already-analyzed versions) is shown outside `<analyze>` tags; new entries are inside.

## Injection

This prompt doc is resolved by ID (`.prompt/analyze/incremental`) during incremental analysis, not by tag matching. It replaces the default analysis prompt when appending new parts to an existing analysis.

## Prompt

You are continuing the analysis of an evolving conversation.
Prior context (already analyzed) is shown outside <analyze> tags.
New entries are inside <analyze> tags.

Identify significant NEW developments in the marked section:
- New themes, decisions, or turning points not already established in the context
- Shifts in direction or approach from what came before
- Important commitments, discoveries, or conclusions

Rules:
- One observation per line, no numbering, no bullets, no preamble
- Synthesize in your own words — never copy or quote the original text
- Be specific: name the actual thing that changed, not abstract categories
- Do not start lines with category labels like "Decision:", "Theme:", "Turning point:"
- Do not include XML tags in your output
- Skip entries that continue an established theme without adding new significance
- If nothing genuinely new: EMPTY
