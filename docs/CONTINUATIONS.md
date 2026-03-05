# Continuations

A continuation is a stateful multi-tick interaction over one API:

```
continue(input) -> output
```

The runtime persists flow state and returns an opaque `cursor` token. Use that cursor on the next tick.

## Core Contract

### Input

```json
{
  "request_id": "optional string",
  "idempotency_key": "optional string",
  "cursor": "optional opaque token; omit to start",

  "goal": "optional string (new flow only)",
  "profile": "optional string (new flow only)",
  "params": {},
  "frame_request": {},
  "steps": [],
  "decision_override": {},
  "response_mode": "standard | debug",

  "overrides": {
    "params": {},
    "frame_request": {},
    "decision_policy": {}
  },

  "work_results": []
}
```

Rules:

1. Start a new flow by omitting `cursor` and providing top-level flow fields (`goal|profile|steps|frame_request`).
2. Resume by sending only `cursor` plus optional `overrides` and `work_results`.
3. Program fields are immutable after flow start. On resume, use `overrides` only.
4. Legacy resume fields (`flow_id`, `state_version`) are rejected.
5. Legacy work-results envelope (`feedback.work_results`) is rejected.

### Output

```json
{
  "request_id": "string",
  "cursor": "opaque token",
  "status": "done | in_progress | waiting_work | paused | failed",
  "frame": {
    "evidence": [],
    "decision": {}
  },
  "work": [],
  "applied_ops": [],
  "errors": []
}
```

When `response_mode` is `debug`, output also includes:

```json
{
  "state": {},
  "output_hash": "sha256 hex",
  "frame": {
    "debug": {
      "slots": {},
      "task": "...",
      "hygiene": [],
      "budget_used": {"tokens": 0, "nodes": 0},
      "status": "..."
    }
  }
}
```

## Status Semantics

`status` is the progression signal (no `next` object):

- `done`: terminal for current flow objective.
- `in_progress`: continue ticking with returned `cursor`; the runtime has additional autonomous work/branch refinement to execute.
- `waiting_work`: execute items in `work`, then submit `work_results`.
- `paused`: idle escalation threshold hit.
- `failed`: invalid input or runtime failure for this tick.

## Work Loop

### Request work

When status is `waiting_work`, inspect `work` entries:

```json
{
  "work_id": "w_abc123",
  "kind": "summarize",
  "executor_class": "local",
  "suggested_executor_id": "",
  "input": {"id": "my-doc"},
  "output_contract": {},
  "quality_gates": {},
  "escalate_if": []
}
```

### Run work

CLI:

```bash
keep continue-work CURSOR WORK_ID
```

Python:

```python
result = kp.continue_run_work(cursor, work_id)
```

### Return work results

Submit at top-level `work_results`:

```json
{
  "cursor": "...",
  "work_results": [
    {
      "work_id": "w_abc123",
      "status": "ok",
      "outputs": {"summary": "..."},
      "quality": {"confidence": 0.9, "passed_gates": true}
    }
  ]
}
```

## Frame Shape

Default frame is intentionally minimal:

- `frame.evidence`: retrieved items for this tick.
- `frame.decision`: discriminator/strategy signals.

Decision support is published at `frame.decision` (version `ds.v1`).

## Applied Mutation Journal

`applied_ops` is flat for both inline and work-originated mutations:

```json
{
  "source": "inline | work",
  "work_id": "w_abc123 or null",
  "op": "upsert_item | set_tags | set_summary | work_result",
  "target": "note id or null",
  "status": "queued | applied | noop | failed",
  "mutation_id": "m_..."
}
```

## Quick Python Loop

```python
result = kp.continue_flow({
    "goal": "query",
    "profile": "query.auto",
    "params": {"text": "authentication"},
    "frame_request": {"seed": {"mode": "query", "value": "authentication"}},
    "work_results": [],
})

while result["status"] in {"in_progress", "waiting_work"}:
    if result["status"] == "waiting_work":
        wr = kp.continue_run_work(result["cursor"], result["work"][0]["work_id"])
        result = kp.continue_flow({"cursor": result["cursor"], "work_results": [wr]})
    else:
        result = kp.continue_flow({"cursor": result["cursor"], "work_results": []})
```

## Concurrency and Idempotency

- `cursor` encodes flow identity + version; stale cursor returns `state_conflict`.
- `idempotency_key` is write-only: it enables replay for retries but is not echoed in output.
