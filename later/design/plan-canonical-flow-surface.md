# Unit 2: Canonical Flow Surface

Date: 2026-03-28
Status: Draft

## Goal

Make canonical flows the source of truth for essential memory operations and make `/v1/flow` the canonical transport surface.

This unit combines the old PRs 4 and 5.

## Problem

Even if the interface is reduced to `run_flow`, the architecture is still unstable if essential operations are implemented twice:

- once as dedicated Python or server logic
- once as flow behavior

That would keep hosted and local semantics at risk of divergence.

## Scope

### In scope

- normalize canonical flow names for essential operations
- route server compatibility endpoints through canonical flows
- remove duplicate semantic implementations for hosted-compatible behavior

### Out of scope

- projection primitives and display shaping
- system-doc role resolution redesign
- local-only admin/integration features

## Canonical Operations

The following operations should exist as canonical flows:

- `get`
- `put`
- `find`
- `tag`
- `delete`
- `move`
- `get-context`
- `get-now`
- `prompt`

If an operation is essential to querying or mutating memory, it should be implemented as a flow first and only wrapped second.

## Primary Design

### 1. Flow definitions are the source of truth

For each essential operation:

- define or normalize the canonical state doc / flow implementation
- make wrappers invoke that canonical flow
- make server endpoints invoke that canonical flow

There must not be a second hosted-compatible implementation path.

### 2. `/v1/flow` becomes authoritative

The daemon/server API should treat `/v1/flow` as the canonical semantic entry point.

Dedicated endpoints like:

- `/v1/notes/{id}`
- `/v1/notes`
- `/v1/search`
- `/v1/notes/{id}/tags`

can remain temporarily, but only as compatibility shims that translate requests into canonical flow invocations server-side.

### 3. Remove transport-semantic drift

The server must preserve all flow parameters consistently:

- `writable`
- `budget`
- `cursor_token`
- `state_doc_yaml`
- projection or rendering parameters added later

The transport layer cannot decide to ignore or reinterpret them.

## File Plan

### [keep/state_doc.py](/Users/hugh/play/keep/keep/state_doc.py)

- define or normalize canonical flow loading for essential operations
- remove cases where essential flows quietly fall back to ad hoc Python logic

### [keep/api.py](/Users/hugh/play/keep/keep/api.py)

- ensure wrappers and internal helpers route through canonical flows
- delete duplicate hosted-compatible semantic logic that survives Unit 1

### [keep/builtin_state_docs.py](/Users/hugh/play/keep/keep/builtin_state_docs.py)

- keep only bootstrap canonical definitions needed during transition
- avoid growing permanent Python-owned semantic definitions

### [keep/daemon_server.py](/Users/hugh/play/keep/keep/daemon_server.py)

- route compatibility endpoints through a single internal flow execution helper
- ensure `/v1/flow` is the authoritative semantic path

### [keep/remote.py](/Users/hugh/play/keep/keep/remote.py)

- keep the client aligned with the canonical flow names and parameters

## Sequencing

1. Normalize the canonical flow names.
2. Make wrappers and local backend methods call those canonical flows only.
3. Add a single internal server helper that executes canonical flow requests.
4. Reimplement compatibility endpoints in terms of that helper.
5. Delete duplicate hosted-compatible implementations.

## Acceptance Criteria

- Every essential memory operation can be traced to one canonical flow path.
- `/v1/flow` is the authoritative semantic interface.
- Dedicated REST endpoints are compatibility shims, not separate implementations.
- Hosted-compatible behavior no longer diverges between local and remote code paths.

## Risks

### Hidden duplicate behavior

The likely failure mode is leaving behind one or two “temporary” direct implementations. Those become the next source of drift. This unit should remove them aggressively.

### Flow naming churn

Canonical names may change during the refactor. That is acceptable as long as the end state is stable and documented in one place.
