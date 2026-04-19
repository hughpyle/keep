---
tags:
  category: system
  context: tag-description
---
# Tag: `type` — Entity Type

The `type` tag classifies an item by what kind of entity it represents in the knowledge graph. It answers the question "what *is* this thing?" — not what you got out of it (see `kind`) or what speech act it performs (see `act`).

## Values

| Value | What it marks | Example |
|-------|---------------|---------|
| `conversation` | A multi-turn dialogue or chat session | Agent conversation, meeting transcript |
| `paper` | A research paper or academic document | arXiv preprint, conference paper |
| `vulnerability` | A security vulnerability or weakness | CWE entry, CVE report |
| `file` | A source file or document | Source code, config file |
| `person` | A person entity | Collaborator, author |
| `project` | A project entity | Repository, initiative |

This tag is unconstrained — the table above lists common values but new entity types can be created as needed.

## Relationship to `kind` and `act`

| Tag | Question | Examples |
|-----|----------|----------|
| `type` | "What is this entity?" | conversation, paper, vulnerability |
| `kind` | "What kind of content is this?" | learning, breakdown, decision |
| `act` | "What speech act is this?" | assertion, commitment, request |

A single item might be `type=conversation kind=decision act=declaration` — a conversation that records a decision, expressed as a declaration.

## Injection

When `analyze --tags type` is used, the full text of this doc is prepended to the analysis prompt as guide context.

## Prompt

Classify the entity type of this item. Use one of the standard values when applicable: `conversation` (dialogue/chat), `paper` (research/academic), `vulnerability` (security weakness), `file` (source document), `person` (individual), `project` (initiative/repo). If none fit, use a descriptive entity-type noun.

## Examples

```bash
# Tag a conversation
keep put "file:///path/to/transcript.md" -t type=conversation

# Tag a research paper
keep put "file:///path/to/paper.pdf" -t type=paper -t topic=ml

# Tag a vulnerability
keep put "CWE-79: Cross-site Scripting" -t type=vulnerability -t topic=security
```
