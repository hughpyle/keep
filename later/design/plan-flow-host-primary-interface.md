# Unit 1: Flow Host Primary Interface

Date: 2026-03-28
Status: Draft

## Goal

Collapse the current object-oriented public API into a flow-first boundary:

- local and hosted implementations expose the same minimal interface
- shared wrappers sit above that interface
- the client and CLI stop depending on backend-specific semantics

This unit combines the old PRs 1 through 3.

## Why This Unit Comes First

The existing layering is unstable because semantic behavior is split across:

- local `Keeper`
- remote `RemoteKeeper`
- daemon transport endpoints
- CLI command implementations

If `run_flow` is not the primary boundary, later work on projections or system docs will keep being undercut by local-only shortcuts and remote-specific behavior.

## Scope

### In scope

- define a minimal `FlowHostProtocol`
- introduce shared wrapper functions over `run_flow`
- make local and remote backends satisfy that protocol
- make the shipped client path consume the shared wrapper layer

### Out of scope

- projection primitives
- role-based system-doc lookup
- daemon runtime hardening
- local admin/integration separation beyond removing obvious semantic drift

## Primary Design

### 1. Minimal backend contract

Replace the current large public protocol with a minimal flow-host contract:

```python
class FlowHostProtocol(Protocol):
    def run_flow(
        self,
        state: str,
        *,
        params: dict[str, Any] | None = None,
        budget: int | None = None,
        cursor_token: str | None = None,
        state_doc_yaml: str | None = None,
        writable: bool = True,
        projection: dict[str, Any] | None = None,
    ) -> FlowResult: ...

    def close(self) -> None: ...
```

Nothing else is part of the stable hosted-compatible interface.

### 2. Shared wrapper layer

Introduce a shared wrapper module that implements convenience operations on top of `run_flow`.

Examples:

- `get(item_id)` -> `run_flow("get", params={...})`
- `put(...)` -> `run_flow("put", params={...})`
- `find(...)` -> `run_flow("find", params={...})`
- `tag(...)` -> `run_flow("tag", params={...})`
- `delete(...)` -> `run_flow("delete", params={...})`
- `get_context(...)` -> `run_flow("get-context", params={...})`
- `get_now(...)` -> `run_flow("get-now", params={...})`

These wrappers own only parameter normalization and response coercion. They do not contain semantic behavior.

### 3. Local backend becomes a flow host

The local `Keeper` should still expose compatibility methods, but those methods become thin adapters over `run_flow`.

The local backend remains responsible for:

- building the flow environment
- executing actions against the local storage implementation
- returning `FlowResult`

It is not responsible for preserving a second semantic API above flows.

### 4. Remote backend becomes a flow transport

`RemoteKeeper` should primarily transport `run_flow` to the hosted service.

Its compatibility methods should use the same shared wrapper layer as the local backend. It should stop carrying distinct semantics such as special `get_now` behavior or alternate interpretations of common operations.

### 5. One client path

The shipped CLI should no longer split between:

- direct local `Keeper` calls
- custom HTTP calls
- separate `RemoteKeeper` semantics

It should use one shared client/wrapper path above `FlowHostProtocol`.

## File Plan

### [keep/protocol.py](/Users/hugh/play/keep/keep/protocol.py)

- add `FlowHostProtocol`
- shrink the notion of the stable public backend interface
- move larger legacy protocol definitions behind compatibility typing only, or remove them if feasible

### New [keep/flow_client.py](/Users/hugh/play/keep/keep/flow_client.py)

- add shared wrapper functions and coercion helpers
- define the canonical client-side adapter surface

### [keep/api.py](/Users/hugh/play/keep/keep/api.py)

- make public compatibility methods delegate to `run_flow`
- remove semantic duplication from local wrapper methods

### [keep/remote.py](/Users/hugh/play/keep/keep/remote.py)

- keep `run_flow` transport as the primary implementation
- route compatibility methods through shared wrappers

### [keep/thin_cli.py](/Users/hugh/play/keep/keep/thin_cli.py)

- switch command implementations to the shared wrapper/client path where practical
- stop growing command-specific semantics in CLI code

### [keep/cli.py](/Users/hugh/play/keep/keep/cli.py)

- reduce or isolate legacy backend construction paths
- stop treating local `Keeper` creation as the default source of semantic behavior for hosted-capable commands

## Sequencing

1. Add `FlowHostProtocol`.
2. Add shared wrapper functions without changing behavior.
3. Convert local `Keeper` compatibility methods to wrapper-style adapters.
4. Convert `RemoteKeeper` compatibility methods to the same wrappers.
5. Switch CLI code to the shared wrapper layer.
6. Delete dead remote/local semantic forks exposed by that conversion.

## Acceptance Criteria

- There is one stable hosted-compatible interface: `run_flow`.
- Local and remote backends share one wrapper layer above `run_flow`.
- Public methods like `get`, `put`, and `find` are visibly thin adapters, not full semantic implementations.
- The CLI no longer depends on a separate semantic path for local and hosted memory operations.

## Risks

### Compatibility fallout

Type and call-site churn may be broad. This is acceptable because the churn is the point: it exposes where the codebase still assumes a large backend object instead of a flow host.

### Incomplete migration

If any high-traffic command keeps a direct semantic path outside the wrapper layer, the old drift will survive. The unit is not done until those paths are either migrated or explicitly classified as local-only.
