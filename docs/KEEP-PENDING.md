# keep pending

Manage the background daemon and its work queues.

## Usage

```bash
keep pending                  # Start the daemon, process work, tail progress
keep pending --list           # Show queue status + active mirrors
keep pending --reindex        # Enqueue all notes for re-embedding
keep pending --retry          # Reset failed items back to pending
keep pending --purge          # Delete all pending work items
keep pending --stop           # Stop the daemon
```

## What it does

`keep pending` starts the background daemon (if not already running) and processes queued work: embedding, summarization, analysis, OCR, link extraction, and edge processing. It tails progress to the terminal so you can watch items being processed.

The daemon also services:

- **File and directory watches** registered via `keep put --watch`
- **Markdown sync mirrors** registered via `keep data export --sync`
- **Timer-driven background work** such as replenishment and retry cycles

When there's no work and no active watches or mirrors, the daemon exits after an idle timeout.

## Flags

### `--list` / `-l`

Show the current state of the work queue without starting the daemon:

```bash
keep pending --list
```

Output includes:
- Pending, processing, and failed item counts
- Active markdown mirrors with their status
- Whether the daemon is running

### `--reindex`

Enqueue every note in the store for re-embedding. Use this after changing embedding providers or models, or to rebuild the search index from scratch:

```bash
keep pending --reindex
```

This queues work — it doesn't block. Run `keep pending` afterward (or let an existing daemon pick it up) to process the queue.

### `--retry`

Reset failed items back to pending so they'll be retried:

```bash
keep pending --retry
```

Useful after fixing a transient issue (e.g., a provider was down, Ollama wasn't running) that caused items to fail.

### `--purge`

Delete all pending work items from the queue:

```bash
keep pending --purge
```

Use with care — this discards queued work permanently. Items that were already processed are unaffected.

### `--stop`

Stop the background daemon:

```bash
keep pending --stop
```

Sends SIGTERM and waits up to 10 seconds for graceful shutdown. If the daemon is stuck, it falls back to SIGKILL. Also cleans up stale discovery files (port, token, PID).

## Markdown mirrors

When markdown sync mirrors are registered (`keep data export --sync`), the daemon services them automatically. `keep pending` and `keep pending --list` report the number of active mirrors:

```
Markdown mirrors active: 1
```

Mirror state (last run, pending changes, errors) is visible via:

```bash
keep data export --list
```

See [keep data](KEEP-DATA.md) for full sync documentation.

## Common workflows

### After import

```bash
keep data import backup.json
keep pending                    # Process embeddings for imported notes
```

### After changing embedding provider

```bash
keep config --setup             # Change provider
keep pending --reindex          # Queue re-embedding for all notes
keep pending                    # Process
```

### Troubleshooting failed items

```bash
keep pending --list             # Check for failed items
keep pending --retry            # Reset them to pending
keep pending                    # Reprocess
```

### Restarting the daemon

```bash
keep pending --stop
keep pending                    # Fresh start
```
