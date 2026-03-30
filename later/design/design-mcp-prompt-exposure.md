# MCP Prompt Exposure From Prompt Docs

Date: 2026-03-30
Status: Draft

Related:
- [KEEP-PROMPT.md](/Users/hugh/play/keep/docs/KEEP-PROMPT.md)
- [API-SCHEMA.md](/Users/hugh/play/keep/docs/API-SCHEMA.md)

## Problem

Keep already has agent prompts stored as `.prompt/agent/*` notes and
rendered through `keep prompt` and the `keep_prompt` MCP tool. Those
prompts are user-facing and reusable, which makes them a natural fit for
MCP Prompts as well.

The missing piece is exposure control. Today:

- prompt discovery is store-driven
- rendering is store-driven
- MCP Prompt exposure does not exist

We want a prompt doc to be able to opt into MCP Prompt exposure without
introducing a second prompt registry in code.

## Goals

- expose selected `.prompt/agent/*` notes as MCP Prompts
- make exposure store-driven
- keep prompt parameterization simple and explicit
- preserve `keep_prompt` as the general tool surface
- avoid a second schema language for prompt arguments

## Non-goals

- exposing every agent prompt automatically
- prompt list change notifications
- rich per-argument schemas in frontmatter
- semantic remapping such as `topic -> text` or `question -> text`
- replacing `keep_prompt` with MCP Prompts

## Current Behavior

Agent prompts are listed from `.prompt/agent/*` notes and rendered via
`render_prompt()` using prompt-doc content plus optional state-doc
bindings.

The current public parameter surface is already small and literal:

- `text`
- `id`
- `since`
- `until`
- `tags`
- `deep`
- `scope`
- `token_budget`

For MCP Prompt exposure, we only want a small subset of that surface and
we want the prompt doc itself to say which parameters should be shown.

## Design

### 1. Prompt-doc opt-in via `mcp_prompt`

Add a prompt-doc tag named `mcp_prompt`.

If the tag is absent:

- the prompt is not exposed via MCP Prompts

If the tag is present:

- the prompt is exposed via MCP Prompts
- the tag value is parsed as an ordered list of argument names

Preferred form:

```yaml
mcp_prompt:
  - text
  - id
  - since
```

Compatibility form:

```yaml
mcp_prompt: text,id,since
```

Both forms should normalize to the same ordered argument list.

Example:

```yaml
---
tags:
  category: system
  context: prompt
  state: get
  mcp_prompt:
    - text
    - id
    - since
---
```

The order in `mcp_prompt` is the order advertised over MCP.

### 2. Supported argument names are literal

The first cut supports only literal pass-through argument names:

- `text`
- `id`
- `since`
- `token_budget`

These map directly to the existing `render_prompt()` parameters with no
renaming or extra interpretation.

That means:

- `text` stays `text`
- `id` stays `id`
- `since` stays `since`
- `token_budget` stays `token_budget`

No alias layer is introduced.

This is intentionally minimal. It avoids inventing semantic wrappers
such as `topic`, `question`, or named schemas.

### 3. All MCP Prompt arguments are optional

For MCP Prompt exposure, all advertised arguments are optional.

Even when a prompt is most useful with `text`, omitting it does not
produce a structurally invalid render. It may produce less useful
context, but that is acceptable and simpler than introducing required
argument metadata.

This means there is no `mcp_required` tag in the initial design.

### 4. MCP Prompt listing is data-driven

`prompts/list` should inspect `.prompt/agent/*` notes and include only
those whose tags include `mcp_prompt`.

For each exposed prompt:

- `name` comes from the note ID suffix
- `description` comes from the existing prompt summary extraction
- `arguments` come from parsing `mcp_prompt`

Argument descriptions can be supplied from a small built-in map:

- `text`: Optional text or query used for prompt context
- `id`: Optional note ID for `{get}` context
- `since`: Optional lower time bound for contextual search
- `token_budget`: Optional token budget for prompt-context rendering

This keeps the docs and UI readable without making prompt docs carry a
second metadata block.

### 5. MCP Prompt rendering is a thin wrapper over existing prompt flow

`prompts/get` should:

- take the prompt `name`
- accept the advertised optional arguments
- pass them through to the existing prompt-rendering flow
  (`render_prompt(text=..., id=..., since=..., token_budget=...)`)
- return one MCP message:
  `role="user"` with text content equal to the expanded prompt text

No separate rendering logic should be introduced for MCP Prompts.

The MCP Prompt path and the `keep_prompt` tool should both use the same
backend rendering behavior.

The text returned in that one message should match what the existing
prompt pipeline already emits after expansion, equivalent to the text
that `expand_prompt()` returns for the current `keep_prompt` tool path.

### 6. Keep `keep_prompt` as the full-power surface

The MCP Prompt surface is intentionally narrower than `keep_prompt`.

`keep_prompt` remains the place for the broader option set:

- `until`
- `tags`
- `deep`
- `scope`

This preserves compatibility with agents and clients that do not surface
MCP Prompts well, and it avoids overloading prompt UIs with too many
parameters.

## Initial Scope

The initial bundled prompts exposed over MCP should be:

- `reflect`
- `conversation`
- `query`

Recommended prompt-doc tags:

- `reflect`: `mcp_prompt: [text, id, since, token_budget]`
- `conversation`: `mcp_prompt: [text, id, since, token_budget]`
- `query`: `mcp_prompt: [text, since, token_budget]`

This note only commits to annotating those three bundled prompts in the
first cut.

## Why Not Richer Prompt Metadata

Two alternatives were considered and rejected for the first version.

### Named schemas

Example:

```yaml
mcp_schema: reflect-v1
```

This adds indirection without enough benefit. The supported parameter
surface is already small, and named schemas would require an extra code
registry for prompt-doc authors to understand.

### Rich metadata block in frontmatter

Example:

```yaml
mcp:
  expose: true
  args:
    - name: text
      required: false
```

Keep's frontmatter handling is intentionally flat and tag-oriented. A
rich nested metadata structure would be a new schema layered on top of a
system that currently treats prompt metadata as tags. That is possible
later, but it is not justified for the first version.

## Example

```markdown
---
tags:
  category: system
  context: prompt
  state: get
  mcp_prompt:
    - text
    - id
    - since
    - token_budget
---
# .prompt/agent/reflect

Reflect on current actions, commitments, and intentions.

## Prompt

{get}
{find}

Review the context above and reflect carefully.
```

This prompt would appear in MCP Prompt listings with four optional
arguments:

- `text`
- `id`
- `since`
- `token_budget`

The rendered result would still come from the normal keep prompt
pipeline and would be returned as a single MCP `user` message with text
content.

## Open Questions

- Should unsupported argument names be ignored with a warning, or make
  the prompt ineligible for MCP exposure? Ignoring with a warning is
  likely friendlier.
- Should user-edited prompt docs be able to remove MCP exposure from a
  bundled prompt by deleting `mcp_prompt`? Probably yes, since prompt
  docs are already authoritative.

## Recommendation

Implement MCP Prompt exposure as an opt-in property of `.prompt/agent/*`
notes using a flat `mcp_prompt` tag with ordered argument names.

Keep the supported argument set small and literal:

- `text`
- `id`
- `since`
- `token_budget`

Keep all of them optional. Keep `keep_prompt` as the broader tool
surface. This gives keep a clean MCP Prompt integration without adding a
second prompt-definition system.
