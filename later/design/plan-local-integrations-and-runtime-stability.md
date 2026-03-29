# Unit 4: Local Integrations And Runtime Stability

Date: 2026-03-28
Status: Draft

## Goal

Cleanly separate local integration concerns from the semantic core, then harden the local runtime around the simplified flow-first interface.

This unit combines the old PRs 8 through 10.

## Problem

Once the semantic core is simplified, the remaining risks are:

- local integrations still bypassing the flow system
- daemon/runtime code still acting like a semantic owner
- lack of contract tests proving local and hosted behavior match

This unit finishes the separation and then stabilizes the local runtime around it.

## Scope

### In scope

- classify and isolate local-only integration/admin concerns
- remove semantic bypasses from local integration code
- harden daemon and queue runtime behavior
- add contract tests across local, daemon, and hosted-style flow hosts

### Out of scope

- changing the primary interface again
- expanding the hosted-compatible semantic surface beyond `run_flow`

## Primary Design

### 1. Local integrations are outside the semantic core

The following concerns should be explicit local integrations:

- daemon lifecycle
- queue inspection and management
- directory scanning
- file watching
- import/export if it remains store-local rather than hosted-capable
- repair/doctor/log tooling

These components may call canonical flow wrappers for semantic work, but they should not read local storage directly to implement hosted-compatible behavior.

### 2. Daemon is a flow host runtime

After Units 1 through 3, the daemon should have a simple role:

- host flow execution for a local store
- provide stable transport
- manage local background runtime concerns

It should not contain parallel semantic behavior.

### 3. Contract testing is the architectural lock

The architecture is only stable if one shared behavior matrix passes against:

- local in-process flow host
- local daemon flow host
- hosted-style remote flow host

Without that, semantic drift will reappear.

## File Plan

### [keep/thin_cli.py](/Users/hugh/play/keep/keep/thin_cli.py)

- isolate or rename clearly local-only commands
- stop silent fallback from hosted-capable commands into local store operations

### [keep/cli.py](/Users/hugh/play/keep/keep/cli.py)

- reduce remaining legacy/local command glue
- keep local-admin behavior explicit

### [keep/_background_processing.py](/Users/hugh/play/keep/keep/_background_processing.py)

- ensure background processing interacts with memory semantics through canonical flow paths where appropriate

### [keep/work_queue.py](/Users/hugh/play/keep/keep/work_queue.py)

- complete thread-safety and transactional hardening
- keep the queue implementation boring and predictable

### [keep/daemon_server.py](/Users/hugh/play/keep/keep/daemon_server.py)

- ensure startup, shutdown, and request handling are transport-stable
- preserve flow-parameter semantics consistently

### [keep/_daemon_client.py](/Users/hugh/play/keep/keep/_daemon_client.py)

- keep discovery and startup conservative and recoverable
- avoid discovery-file and health-check races

### Tests

- [tests/test_flow_integration.py](/Users/hugh/play/keep/tests/test_flow_integration.py)
- [tests/test_daemon_server.py](/Users/hugh/play/keep/tests/test_daemon_server.py)
- [tests/test_cli.py](/Users/hugh/play/keep/tests/test_cli.py)
- new contract tests for shared behavior across all flow-host implementations

## Sequencing

1. Classify commands and modules as hosted-compatible semantic core or local integration/admin.
2. Remove remaining semantic bypasses from local integration code.
3. Harden daemon lifecycle, queue access, and transport correctness.
4. Add the shared contract suite and make it mandatory for future changes.

## Acceptance Criteria

- Hosted-capable commands never silently bypass the flow host.
- Local-only commands are explicit in code and user-facing behavior.
- The daemon is transport/runtime infrastructure, not a second semantic engine.
- Queue and daemon behavior are stable under concurrent and recovery scenarios.
- One contract suite proves shared behavior across local, daemon, and hosted-style flow hosts.

## Risks

### Stability work too early

Daemon hardening before semantic simplification would waste effort on the wrong boundary. This unit assumes Units 1 through 3 have already reduced the surface area.

### Local admin ambiguity

If commands remain semantically ambiguous between hosted-capable and local-only behavior, the user model will stay confusing. The command boundary should become explicit even if that means renaming commands or adding clearer namespaces.
