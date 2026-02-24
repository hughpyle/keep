# keep prompt

Render an agent prompt with injected context from the store.

## Usage

```bash
keep prompt --list                        # List available prompts
keep prompt reflect                       # Render the reflect prompt
keep prompt reflect "auth flow"           # With search context
keep prompt reflect --id %abc123          # Context from specific item
keep prompt reflect --since P7D           # Recent context only
keep prompt reflect --tag project=myapp   # Scoped to project
```

## Options

| Option | Description |
|--------|-------------|
| `--list`, `-l` | List available agent prompts |
| `--id ID` | Item ID for `{get}` context (default: `now`) |
| `--tag`, `-t` KEY=VALUE | Filter search context by tag (repeatable) |
| `--since DURATION` | Only items updated since (ISO duration or date) |
| `--until DURATION` | Only items updated before (ISO duration or date) |
| `-n`, `--limit N` | Max search results (default: 5) |

## Template placeholders

Prompt docs may contain placeholders that are expanded at render time:

| Placeholder | Expands to |
|-------------|------------|
| `{get}` | Full context for `--id` target (default: `now`) — YAML frontmatter with similar items, meta sections, version history |
| `{find}` | Search results for the text argument — summary lines matching the query with optional tag/time filters |

When no text argument is given, `{find}` expands to empty. When no `--id` is given, `{get}` shows the `now` document context.

## Prompt docs

Agent prompts live in the store as `.prompt/agent/*` system documents. They use the same `## Prompt` section format as other keep prompt docs, but contain agent-facing instructions rather than LLM system prompts.

Bundled prompts, loaded on first use:

| Prompt | ID | Purpose |
|--------|----|---------|
| `reflect` | `.prompt/agent/reflect` | Full structured reflection practice |
| `session-start` | `.prompt/agent/session-start` | Context injection at session start |
| `subagent-start` | `.prompt/agent/subagent-start` | Context injection for subagent initialization |

### Viewing and editing

```bash
keep get .prompt/agent/reflect            # View the prompt doc
```

Prompt docs are editable — they version like any other store document. User edits are preserved across upgrades (content-hash detection).

## keep reflect

`keep reflect` is an alias for `keep prompt reflect`. It accepts the same text argument and `--id` option:

```bash
keep reflect                              # Same as: keep prompt reflect
keep reflect "auth flow"                  # Same as: keep prompt reflect "auth flow"
keep reflect --id %abc123                 # Same as: keep prompt reflect --id %abc123
```

## See Also

- [KEEP-MCP.md](KEEP-MCP.md) — MCP server (`keep_prompt` tool)
- [KEEP-NOW.md](KEEP-NOW.md) — Current intentions
- [AGENT-GUIDE.md](AGENT-GUIDE.md) — Working session patterns
- [REFERENCE.md](REFERENCE.md) — Quick reference index
