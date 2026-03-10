# Flow System Convergence

Date: 2026-03-09
Updated: 2026-03-10
Status: Design note

## Current state

Two parallel flow systems:

1. **FlowEngine** (`flow_engine.py`, `flow.py`, `work_store.py`) — original
   continuation engine. Drives the write path (after-write processing). Full
   lifecycle: cursors, work items, mutations, idempotency, optimistic
   concurrency. Heavy. Nobody uses the `continue`/`continue-work` CLI
   entrypoints directly.

2. **State doc runtime** (`state_doc_runtime.py`) — lightweight runtime.
   Drives the read path (get-context, find-deep, query-resolve). Runs
   synchronously to completion. No cursors, no work items. Already has
   an action registry (`actions/`) with read and write actions (find, get,
   put, tag, summarize, analyze, traverse, etc.).

## Target

Converge on state doc runtime as the single flow model:

- **`keep flow`** replaces `keep continue` / `keep continue-work`.
- `run_flow` returns a self-contained cursor when it stops. Cursor encodes
  current state name, tick count, params, and accumulated bindings.
- `run_flow` accepts a cursor to resume — restores state and continues.
- FlowEngine becomes purely the work lifecycle manager (enqueue, claim,
  execute, complete). State doc runtime handles all flow logic.

## CLI design

```
keep flow <state> [--target ID] [--budget N]     # run a stored state doc
keep flow --file path.yaml [--target ID]          # run from file
keep flow --file - [--target ID]                  # run from stdin
keep flow --cursor <token> [--budget N]           # resume a stopped flow
```

State doc source (in priority order):
1. `--file path` or `--file -` (stdin) — parse YAML directly
2. `<state>` argument — load `.state/<name>` from the store

The `--target` param sets `params.id`, making the target note available
to all actions in the flow.

Output is JSON: `{status, bindings, cursor?, data?}`.  When status is
`stopped`, the cursor field contains a resumable token.

## Cursor design

Self-contained, no database. Base64url-encoded JSON:

```json
{
  "s": "query-explore",
  "t": 3,
  "b": {"search": {"results": [...], "margin": 0.04}}
}
```

Fields: **s**tate (where to resume), **t**icks (consumed so far, for
diagnostics), **b**indings (accumulated results from previous ticks).

The cursor is the flow's internal checkpoint — opaque to the caller.

Why self-contained:
- No continuation.db or flow records needed
- Works across CLI invocations, MCP calls, and piped workflows
- The caller owns the cursor — inspect it, modify params, discard it
- Stateless server: the runtime doesn't need to track in-progress flows

Trade-off: cursors can be large if bindings contain search results. For
multi-step query flows with many results, a cursor might be several KB.
This is fine for CLI/MCP use; for HTTP APIs, consider a `--persist` flag
that stores the cursor in the work_store and returns a short token.

## Separation of concerns

Three things are always provided by the caller, never carried in the cursor:

- **Params** (`-p key=value`) — the caller's intent: query, thresholds,
  target note. Fresh params override what the next tick sees. This is how
  the caller steers: review partial results, adjust the query or thresholds,
  and resume.
- **Budget** (`--budget N`) — resource allocation, always per-invocation.
  Defaults to `budget_per_flow` from config (default: 5).
  A stopped flow resumed with `--budget 5` gets 5 fresh ticks. The cursor's
  `ticks` field is historical (for diagnostics), not a remaining balance.
- **State doc source** — the flow definition. On resume this comes from
  the cursor (which names the next state), but the caller can also provide
  `--file` to inject a different state doc mid-flow.

The cursor carries only what the flow produced: which state it stopped at
and the accumulated bindings from previous ticks.

```
Cursor (flow's state)     +  Params (caller's intent)  +  Budget (caller's allocation)
  where to resume               what to search for           how many ticks this call
  what was found so far          what thresholds to use
```

## Write-capable action context

The current `_EnvActionContext` is read-only (summarize/tag/analyze raise
NotImplementedError on provider resolution). To run write flows (after-write,
custom processing), the action context needs:

- `resolve_provider(kind, name)` — delegate to Keeper's provider registry
- `put(content, id, tags)` — write back to the store
- `tag(id, tags)` — update tags on an existing note

The `LocalWorkExecutor` in `flow_executor.py` already has a write-capable
context. Factor out the provider wiring so both paths can use it.

## Migration

1. Add `keep flow` command with file/stdin/stored state doc support
2. Wire write-capable action context into state_doc_runtime
3. Add cursor encoding/decoding to `run_flow`
4. Deprecate and hide `keep continue` / `keep continue-work`
5. Migrate after-write dispatch to use state_doc_runtime + work lifecycle
6. Remove FlowEngine flow logic (frame/decision/work pipeline)

Steps 1-4 are immediate. Steps 5-6 are future work — the FlowEngine
write path still functions and doesn't need to be rushed.

## Use cases

1. **Resume after terminal** — flow returned `stopped: ambiguous` or
   `stopped: budget`. Agent reviews partial results, pushes further with
   more budget or different strategy.

2. **Run a built-in flow** — agent invokes a state doc directly:
   `keep flow after-write --target %abc123` to re-process a note.

3. **Run a custom flow** — user writes a YAML state doc for their own
   workflow (commitment review, weekly reflection, bulk retag) and runs it:
   `keep flow --file review.yaml --target myproject`

4. **Pipe inline state docs** — agent generates a state doc on the fly
   and pipes it in: `echo "..." | keep flow --file - --target myproject`

## Resolved questions

- [x] Cursor encoding: **self-contained** (state + ticks + bindings),
  not persistent. No database for cursor storage.
- [x] Resumed flows **share bindings** from the previous run. The cursor
  carries accumulated state — that's the point.
- [x] Budget is **per-invocation**, not carried in the cursor. The cursor's
  tick count is diagnostic only.
- [x] Steering is via **fresh params** alongside the cursor. Params and
  budget are the caller's domain; the cursor is the flow's domain.
- [ ] Write-path migration: when does FlowEngine's frame/decision/work
  pipeline move to state doc evaluation?
