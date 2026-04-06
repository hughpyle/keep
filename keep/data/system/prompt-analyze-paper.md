---
tags:
  category: system
  context: prompt
---
# .prompt/analyze/paper

Analysis prompt for research papers and other long-form structured documents tagged `type=paper`. Decomposes along the paper's actual sections rather than producing narrative commentary.

## Injection

This is a prompt doc with a match rule: `type=paper`. When a document tagged `type=paper` is analyzed, this `## Prompt` section overrides the default. More match rules = higher specificity = higher priority.

type=paper

## Prompt

Decompose this paper along its actual sections, in order. Content is wrapped in <content> tags; only extract from content inside <analyze> tags.

For each section that is present, write ONE LINE stating what the section actually contains — datasets named, methods compared, claims made, numbers reported, entities mentioned. Use the paper's own headings as anchors when visible. Common sections include: introduction, related work, background, problem statement, methods, approach, datasets, experiments, evaluation, results, discussion, limitations, conclusion, future work, references.

Rules:
- One observation per line, no numbering, no bullets, no preamble
- Synthesize in your own words — never copy or quote the original text
- Be specific: name the actual datasets, methods, models, metrics, and findings — not abstract categories
- Do not analyze, critique, recommend, or comment on the paper as a whole
- Do not start lines with category labels like "Section:", "Topic:", "Recommendation:"
- Do not include XML tags in your output
- Skip front matter, author affiliations, acknowledgments, and bibliographic boilerplate
- Skip sections that aren't present — do not invent placeholders for missing sections
- If nothing substantive: EMPTY
