---
tags:
  category: system
  context: tag-description
  _inverse: cited_by
---
# Tag: `cites` — Bibliographic Citation

The `cites` tag records a deliberate bibliographic citation edge: document A *cites* document B in the academic, legal, or scholarly sense. It is distinct from `references` (which captures any link extracted from body content) and from `informs` (which captures provenance — "source X fed into note Y").

The `_inverse` declaration means this tag creates a navigable relationship edge. If paper A has `cites: B`, then `get B` will show A under `cited_by:`.

## When to use `cites` vs related tags

| Tag | Meaning | Populated by | Example |
|-----|---------|--------------|---------|
| `references` | A mentions / links to B (any parseable link) | `extract_links` action, scanning body content | "See [the arxiv paper](https://arxiv.org/abs/2403.04782)" anywhere in a note |
| `cites` | A formally cites B as bibliographic attribution | Deliberate enrichers (Semantic Scholar, CrossRef, DOI resolvers, Works Cited parsers) | A research paper listing B in its bibliography |
| `informs` | B was a source for A (temporal provenance) | Agents that write A after reading B | "I read X and distilled it into this note" |

Use `cites` only when the relationship is a *formal citation*: the edge exists because B appears in A's curated bibliography, reference list, or a structured citation database — not because B's URL happens to appear in A's body text. Casual mentions belong in `references`.

## Characteristics

- **Edge-creating**: The `_inverse: cited_by` declaration makes this a navigable edge tag.
- **Auto-vivifying**: If the target doesn't exist as an item, it is created automatically.
- **Multi-valued**: A paper can cite many works.
- **Not set by `extract_links`**: `extract_links` populates `references`, not `cites`. The distinction is deliberate — link extraction is mechanical; citation recognition is inferential. Enrichers that understand bibliographic structure should set `cites` directly.

## Usage

```bash
# A research-paper enricher sets cites with display aliases:
keep tag 'https://arxiv.org/abs/2403.04782' \
  -t 'cites=[[https://arxiv.org/abs/2201.08236|Temporal KGs Survey 2022]]' \
  -t 'cites=[[https://doi.org/10.1145/3460231.3474243|TKG Reasoning (SIGIR 2021)]]'

# See who cites a paper
keep get https://arxiv.org/abs/2201.08236
# → cited_by:
# →   - https://arxiv.org/abs/2403.04782  [2024-03-11] Temporal Knowledge Graph: …
```

Aliases on the tag value (`[[target|Label]]`) are picked up by the edge processor's labeled-edge enrichment and seed the target note's `name` tag, so citation target stubs render with their paper title instead of a bare URL.
