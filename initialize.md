# Initialization

The store initializes automatically when you create an `AssociativeMemory` instance.

## Default Store Location

The store defaults to `.assocmem/` at the git repository root:
- Walks up from current directory to find `.git/`
- Creates `.assocmem/` there if it doesn't exist
- Falls back to `.assocmem/` in cwd if not in a git repo

Override with `ASSOCMEM_STORE_PATH` environment variable or explicit path argument.

**Note:** Add `.assocmem/` to your `.gitignore` if the store should not be committed.

## Quick Start

```bash
# Install in a venv
python -m venv .venv
source .venv/bin/activate
pip install assocmem[local]
```

```python
from assocmem import AssociativeMemory

# Uses .assocmem/ at repo root by default
mem = AssociativeMemory()
```

## CLI

```bash
# Initialize and verify
python -m assocmem init

# Or specify store explicitly
python -m assocmem init --store /path/to/store
```

## Configuration

On first run, `assocmem.toml` is created in the store directory with auto-detected providers:

- **Apple Silicon**: MLX for embedding/summarization/tagging
- **With OpenAI key**: OpenAI for summarization/tagging, sentence-transformers for embedding
- **Fallback**: sentence-transformers for embedding, passthrough summarization, no tagging

Edit the TOML to override provider choices.
