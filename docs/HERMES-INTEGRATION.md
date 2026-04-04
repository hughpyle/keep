# Hermes Agent Integration

Keep provides a memory provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Once configured, Hermes gets persistent reflective memory across sessions — semantic search, conversation versioning, and agent prompts.

## Setup

```bash
# 1. Install keep-skill into the Hermes environment
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python3 keep-skill

# 2. Run the setup wizard
hermes memory setup
# Select "keep", choose embedding/summarization providers

# 3. Start a new Hermes session
hermes
```

The first session initializes the store, migrates system docs, and presents the agent with the reflective memory practice guide. The agent should follow the instructions in the nowdoc — reading the practice guide, the foundational teachings, and then reflecting.

## What the agent sees

On first session, the system prompt includes the nowdoc with step-by-step instructions to read the practice guide (`keep_help`), read the library teachings (`keep_flow get`), and reflect (`keep_prompt reflect`). After the agent completes this and updates `now`, subsequent sessions show its own working context instead.

Three tools are available: `keep_flow` (all operations), `keep_help` (documentation), `keep_prompt` (context-injected prompts).

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
