---
tags:
  category: system
  context: tag-description
---
# Tag: `type` — Content Classification

The `type` tag classifies an item by what kind of content it is. It is orthogonal to `act` (which classifies the speech act) and `topic`/`project` (which classify subject matter).

An item can have both `type` and `act`. For example, `type=breakdown act=assessment` — the content is a breakdown, and the speech act is an evaluation.

## Values

| Value | What it marks | Example |
|-------|---------------|---------|
| `learning` | Hard-won insight worth remembering | "Token refresh fails when clock skew exceeds 30s" |
| `breakdown` | A failure where assumptions were revealed | "Assumed user wanted full rewrite, actually wanted minimal patch" |
| `gotcha` | A known trap or non-obvious pitfall | "ChromaDB silently drops metadata keys with None values" |
| `reference` | An indexed document, file, or URL | A PDF, webpage, or source file captured for later retrieval |
| `teaching` | Source material for study or practice | Texts, sutras, foundational documents |
| `meeting` | Notes from a meeting or conversation | "Meeting notes: discussed auth approach with team" |
| `pattern` | A recognized recurring structure | "Incremental Specification: propose interpretation, get correction, repeat" |
| `possibility` | An exploration of options, no commitment yet | "Three auth options explored: OAuth2, API keys, magic links" |
| `decision` | A significant choice with recorded reasoning | "Chose PKCE over implicit flow for security" |

## Relationship to `act`

The `type` tag describes the *content*. The `act` tag describes the *speech act*. They answer different questions:

- `type=learning` — "What kind of thing is this?" → A learning.
- `act=assertion` — "What is the speaker doing?" → Stating a fact.

A single item might be `type=learning act=assessment` — a learning that takes the form of an evaluation.

## Examples

```bash
# Capture a learning
keep put "Mock time in tests instead of real sleep assertions" -t type=learning -t topic=testing

# Record a breakdown
keep put "Assumed X, actually Y. Next time: Z" -t type=breakdown -t project=myapp

# Index a reference document
keep put "file:///path/to/design.pdf" -t type=reference -t topic=architecture

# Note a gotcha for future agents
keep put "sqlite WAL mode required for concurrent reads" -t type=gotcha -t topic=database

# Record a decision
keep put "Chose LanceDB over Pinecone: local-first, no API dependency" -t type=decision -t project=keep
```
