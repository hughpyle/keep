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
| `after_compaction` | Context compacted | Indexes `memory/` dir + `MEMORY.md` into keep via `keep put` |

The agent starts each turn knowing its current intentions, similar items, open commitments, and recent learnings.

After compaction, workspace memory files are automatically indexed into keep. This uses the file-stat fast-path (mtime+size check) — unchanged files are skipped without reading. Safe to run on every compaction.

## Reinstall / Upgrade

After upgrading keep, reinstall the plugin:

```bash
openclaw plugins install -l $(keep config openclaw-plugin)
openclaw gateway restart
```

The plugin source lives at `$(keep config openclaw-plugin)` — this resolves to the `openclaw-plugin/` directory inside the installed keep package.

## Recommended: Daily Reflection Cron

For automatic deep reflection with document analysis, create a cron job:

```bash
openclaw cron add \
  --name keep-reflect \
  --cron "0 21 * * *" --tz "America/New_York" \
  --session isolated \
  --agent-turn "Daily memory sync and reflection.

1. Index memory files with analysis: run \`keep put /path/to/workspace/memory/ --analyze\`
2. Review recent work: \`keep list --since P1D\` and \`keep now --history\`
3. What patterns are forming? What commitments are still open? What did you learn?
4. Update MEMORY.md and \`keep now\` with what matters.

This is the deeper review — not just what happened, but what it means."
```

This runs nightly in an isolated session. The `--analyze` flag finds themes within each document; with v0.42.4+'s smart skip, unchanged files cost nothing.

**Three-layer integration:**

| Layer | Trigger | Command | Purpose |
|-------|---------|---------|---------|
| Hook | After compaction | `keep put memory/` | Index files (cheap, hash-skip) |
| Cron | Daily (e.g. 9pm) | `keep put memory/ --analyze` | Find themes, deep reflection |
| Practice | Manual | `keep put`, `keep now` | Intentional capture |

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
