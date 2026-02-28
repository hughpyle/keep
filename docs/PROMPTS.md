# Prompt Overrides

Prompt docs at `.prompt/summarize/*` and `.prompt/analyze/*` let you customize the LLM system prompts used for summarization and analysis. Unlike tag docs (which augment the prompt), prompt docs **replace** the default system prompt entirely.

## How prompt docs work

Each prompt doc has two parts:

1. **Match rules** — tag queries that determine when this prompt applies (same DSL as `.meta/*` docs)
2. **`## Prompt` section** — the actual system prompt text sent to the LLM

When a document is summarized or analyzed, keep scans all `.prompt/{type}/*` docs, finds those whose match rules match the document's tags, and selects the most specific match (most rules matched). The `## Prompt` section from the winner replaces the default system prompt.

## Bundled prompt docs

| ID | Match rule | Purpose |
|----|-----------|---------|
| `.prompt/summarize/default` | *(none — fallback)* | Default summarization prompt |
| `.prompt/summarize/conversation` | `type=conversation` | Preserves dates, names, facts from conversations |
| `.prompt/analyze/default` | *(none — fallback)* | Default analysis prompt for structural decomposition |
| `.prompt/analyze/conversation` | `type=conversation` | Fact extraction from conversations |

## Creating custom prompts

Create a new prompt doc with match rules targeting specific tags:

```bash
# Custom summarization for code documentation
keep put "$(cat <<'EOF'
topic=code

## Prompt

Summarize this code documentation in under 200 words.
Focus on: what the API does, key parameters, return values, and common pitfalls.
Begin with the function or class name.
EOF
)" --id .prompt/summarize/code
```

Match rules can combine multiple tags for higher specificity:

```bash
# Prompt for meeting notes in a specific project
keep put "$(cat <<'EOF'
type=meeting project=myapp

## Prompt

Summarize this meeting in under 300 words.
Focus on decisions made, action items assigned, and deadlines mentioned.
List each action item with its owner.
EOF
)" --id .prompt/summarize/myapp-meetings
```

The most specific match wins — a prompt matching `type=meeting project=myapp` (2 rules) beats one matching just `type=meeting` (1 rule), which beats the default (0 rules).

## Viewing prompt docs

```bash
keep get .prompt/summarize/default      # See the default summarization prompt
keep get .prompt/analyze/conversation   # See the conversation analysis prompt
keep list .prompt                       # All prompt docs
```

## See Also

- [TAGGING.md](TAGGING.md) — Tag descriptions and how `## Prompt` sections work in tag docs
- [META-TAGS.md](META-TAGS.md) — Contextual queries (same match-rule DSL)
- [KEEP-ANALYZE.md](KEEP-ANALYZE.md) — CLI reference for `keep analyze`
- [KEEP-PUT.md](KEEP-PUT.md) — Indexing documents (summarization)
- [REFERENCE.md](REFERENCE.md) — Quick reference index
