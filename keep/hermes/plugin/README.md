# Keep Memory Provider — Hermes Plugin

This directory is the Hermes-side plugin shim for Keep. It gets copied
into `plugins/memory/keep/` inside your Hermes Agent installation.

## Install

```bash
# Automatic — finds Hermes and copies the plugin
python -m keep.hermes.install

# Or manual — copy this directory yourself
cp -r "$(python -c "from pathlib import Path; import keep.hermes.plugin; print(Path(keep.hermes.plugin.__file__).parent)")" \
    /path/to/hermes-agent/plugins/memory/keep
```

## Setup

```bash
hermes config set memory.provider keep
hermes memory setup
```

## How it works

The plugin is a thin typed wrapper that inherits from Hermes's
`MemoryProvider` ABC and delegates all calls to `keep.hermes.KeepMemoryProvider`
(which is duck-typed and has no Hermes imports).

This keeps the keep-skill package independent of Hermes while giving
Hermes plugin discovery a proper `MemoryProvider` subclass to find.
