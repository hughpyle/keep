# OpenClaw Integration

How to install and configure keep as an OpenClaw plugin.

---

## Install keep

```bash
uv tool install keep-skill                    # API providers included
# Or: uv tool install 'keep-skill[local]'    # Local models, no API keys needed
```

## Install the Plugin

```bash
openclaw plugins install -l $(keep config openclaw-plugin)
openclaw plugins enable keep
openclaw gateway restart
```

This installs the lightweight plugin from keep's package data directory.

## What Gets Installed

**Protocol block** — `AGENTS.md` in your OpenClaw workspace gets the keep protocol block appended automatically (on any `keep` command, if `AGENTS.md` exists in the current directory).

**Plugin hooks:**

| Hook | Event | What it does |
|------|-------|-------------|
| `before_agent_start` | Agent turn begins | Runs `keep now -n 10`, injects output as prepended context |
| `after_agent_stop` | Agent turn ends | Runs `keep now 'Session ended'` to update intentions |

The agent starts each turn knowing its current intentions, similar items, open commitments, and recent learnings.

## Reinstall / Upgrade

After upgrading keep, reinstall the plugin:

```bash
openclaw plugins install -l $(keep config openclaw-plugin)
openclaw gateway restart
```

The plugin source lives at `$(keep config openclaw-plugin)` — this resolves to the `openclaw-plugin/` directory inside the installed keep package.

## Optional: Daily Reflection Cron

For automatic deep reflection, create a cron job:

```bash
openclaw cron add \
  --name keep-reflect \
  --cron "0 21 * * *" \
  --session isolated \
  --system-event "Reflect on this day with \`keep reflect\`. Follow the practice."
```

This runs in an isolated session at 9pm daily. Delivery is silent — the value is in what gets written to the store.

## Provider Configuration

keep auto-detects AI providers from environment variables. Set one and go:

```bash
export OPENAI_API_KEY=...      # Simplest (handles both embeddings + summarization)
# Or: GEMINI_API_KEY=...       # Also does both
# Or: VOYAGE_API_KEY=... and ANTHROPIC_API_KEY=...  # Separate services
```

If Ollama is running locally, it's auto-detected with no configuration needed.

For local-only operation (no API keys): `uv tool install 'keep-skill[local]'`

See [QUICKSTART.md](QUICKSTART.md) for full provider options, model configuration, and troubleshooting.
