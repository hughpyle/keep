# keep config

Show configuration and resolve paths.

## Usage

```bash
keep config                           # Show all config
keep config file                      # Config file location
keep config tool                      # Package directory (SKILL.md location)
keep config docs                      # Documentation directory
keep config store                     # Store path
keep config openclaw-plugin           # OpenClaw plugin directory
keep config providers                 # All provider config
keep config providers.embedding       # Embedding provider name
```

## Options

| Option | Description |
|--------|-------------|
| `--reset-system-docs` | Force reload system documents from bundled content |
| `-s`, `--store PATH` | Override store directory |

## Config file location

The config file is `keep.toml` inside the config directory. The config directory is resolved in this order:

1. **`KEEP_CONFIG` environment variable** — explicit path to config directory
2. **Tree-walk** — search from current directory up to `~` for `.keep/keep.toml`
3. **Default** — `~/.keep/`

The tree-walk enables project-local stores: place a `.keep/keep.toml` in your project root and `keep` will use it when you're in that directory tree.

## Store path resolution

The store (where data lives) is resolved separately from config:

1. **`--store` CLI option** — per-command override
2. **`KEEP_STORE_PATH` environment variable**
3. **`store.path` in config file** — `[store]` section of `keep.toml`
4. **Config directory itself** — backwards compatibility default

## Config file format

```toml
[store]
version = 2
max_summary_length = 1000

[embedding]
name = "mlx"                           # or "voyage", "openai", "ollama", "sentence_transformers"
model = "all-MiniLM-L6-v2"

[summarization]
name = "mlx"                           # or "anthropic", "openai", "ollama"
model = "mlx-community/Llama-3.2-3B-Instruct-4bit"

[media]
name = "mlx"                           # or "ollama" (auto-detected)
vision_model = "mlx-community/Qwen2-VL-2B-Instruct-4bit"
whisper_model = "mlx-community/whisper-large-v3-turbo"

[document]
name = "composite"

[tags]
project = "my-project"                 # Default tags applied to all new items
owner = "alice"
```

## Environment variables

```bash
KEEP_STORE_PATH=/path/to/store        # Override store location
KEEP_CONFIG=/path/to/.keep            # Override config directory
KEEP_TAG_PROJECT=myapp                # Auto-apply tags (any KEEP_TAG_* variable)
KEEP_VERBOSE=1                        # Debug logging to stderr
KEEP_NO_SETUP=1                       # Skip auto-install of tool integrations
```

## Config subpaths

| Path | Returns |
|------|---------|
| `file` | Config file path (`~/.keep/keep.toml`) |
| `tool` | Package directory (where SKILL.md lives) |
| `docs` | Documentation directory |
| `store` | Store data path |
| `openclaw-plugin` | OpenClaw plugin directory |
| `providers` | All provider configuration |
| `providers.embedding` | Embedding provider name |
| `providers.summarization` | Summarization provider name |
| `providers.media` | Media description provider name |

Subpath output is raw (unquoted) for shell scripting:

```bash
cat "$(keep config tool)/SKILL.md"    # Read the practice guide
ls "$(keep config store)"             # List store contents
```

## Resetting system documents

System documents (`.conversations`, `.domains`, `.tag/*`, etc.) are bundled with keep and loaded on first use. If they've been modified or corrupted:

```bash
keep config --reset-system-docs       # Reload all from bundled content
```

## See Also

- [QUICKSTART.md](QUICKSTART.md) — Installation and provider setup
- [REFERENCE.md](REFERENCE.md) — Quick reference index
