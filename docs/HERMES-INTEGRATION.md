# Hermes Agent Integration

Keep provides a memory provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Once configured, Hermes gets persistent reflective memory across sessions — semantic search, conversation versioning, and agent prompts.

Currently requires [this branch](https://github.com/NousResearch/hermes-agent/pull/5172).  Check out the branch and run.

If you have Ollama running locally, it will be auto-detected.  For other providers, put the appropriate API keys in your Hermes environment first.  Supports multiple independent profiles.

## Setup

```bash
# 1. Run the setup wizard
hermes memory setup
# Select "keep", choose embedding/summarization providers

# 2. Start a new Hermes session
hermes
```

The first session initializes the store, migrates system docs, and presents the agent with the reflective memory practice guide. The agent should follow the instructions in the nowdoc — reading the practice guide, the foundational teachings, and then reflecting.

## What the agent sees

The agent's system prompt is framed by a Hermes-specific wrapper (`.prompt/agent/system-hermes`) that renders alongside the built-in `memory` tool and the USER PROFILE / MEMORY blocks. It positions keep as the cross-session working memory (`now`) and long-term store, sitting above Hermes' pinned-essentials layer. The wrapper then includes the generic reflective-memory practice (`.prompt/agent/system`), so the same core instructions are available in any host.

On first session, the prefetched user-message context includes the nowdoc with step-by-step instructions to read the practice guide (`keep_help`), read the library teachings (`keep_flow get`), and reflect (`keep_prompt reflect`). After the agent completes this and updates `now`, subsequent sessions show its own working context instead.

Three tools are available: `keep_flow` (all operations), `keep_help` (documentation), `keep_prompt` (context-injected prompts).

For `keep_flow`, the normal form is always `state + params`:

```text
keep_flow(state="get", params={"id": "now"})
keep_flow(state="list", params={"prefix": ".library", "include_hidden": true})
keep_flow(state="put", params={"id": "now", "content": "updated intentions"})
```

`keep_flow(state="get", ...)` returns the requested note in note-first form, including its tags and body, with contextual sections such as similar notes, related/meta sections, parts, and version navigation when available. It is not a bare exact-fetch API.

`state_doc_yaml` is available, but it is an advanced escape hatch for genuinely custom inline flows. Hermes agents, especially lighter models, are more reliable when examples stick to `state + params`.

## Agent behavior

Hermes agents can be reluctant to follow the first-time instructions unprompted. If the agent doesn't engage with the practice guide on its own, ask it directly:

> "Follow the keep instructions in your system prompt."

Once the agent completes the initial practice (reading, reflecting, updating now), it typically generates its own workflow or checklist for using reflective memory effectively. This self-generated practice tends to be more durable than the initial instructions.

## Store location

By default, the setup wizard creates the store at `$HERMES_HOME/keep` (e.g. `~/.hermes/keep`). This is written to `$HERMES_HOME/.env` as `KEEP_STORE_PATH`.

If `KEEP_STORE_PATH` is already set in the environment when `initialize()` runs, the provider uses that path instead. This allows sharing a store across profiles or pointing to an external store, but it overrides the per-profile default.

## Using keep CLI with the Hermes store

To use the `keep` CLI with the Hermes store, set `KEEP_STORE_PATH`:

```bash
export KEEP_STORE_PATH=~/.hermes/keep
keep find "recent work"
keep get now
keep flow stats
```

## Architecture

- **In-process Keeper** handles reads (search, get, prompt rendering) and writes directly — no RPC overhead
- **Background daemon** (auto-started) handles embeddings and summaries asynchronously
- **Per-profile store** — defaults to `$HERMES_HOME/keep`; overridable via `KEEP_STORE_PATH`
- **Conversation versioning** — each turn is stored as a version of a per-channel item
- **Shared-chat grouping** — Hermes gateway sessions are per-user in groups/channels by default (`group_sessions_per_user: true`); set it to `false` in Hermes if you want one shared session per channel/thread
