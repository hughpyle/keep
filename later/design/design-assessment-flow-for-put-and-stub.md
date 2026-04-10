# Assessment Flow For `put` And `stub`

Date: 2026-04-10
Status: Implemented (v0.136.0)

## Problem

We want policy checks on notes that are stored in keep; these checks should be
customizable using the flow mechanism.

This policy applies to direct `put`, as well as automatic stub creation from
references; it does not apply to versioning or analysis parts.

If a caller provides a custom ID for `put(uri=..., id=...)`, retain the custom ID
while assessing the URI.  Existing private/internal URL blocking remains unchanged
in the HTTP document provider. That is a separate network safety guard, not an
assessment policy.

The first policy instance is: URL reputation from VirusTotal.
There will be a "URL assessment by VirusTotal" action and flow processing, with
the following behavior:

- If `malicious > 0`: tag `assessment: malicious`, write explanatory content,
  and stop further processing.
- If `suspicious > 0`: tag `assessment: suspicious`, write the normal content,
  and continue processing.

Other instances later may include: email address reputation, file-hash lookup.

## Why The Existing Boundaries Are Wrong

### `after-write` is too late

`after-write` only runs after the note is already stored. It can enrich or tag,
but it cannot prevent fetch/store side effects or stop the original insert.

### `.state/put` alone is too narrow

The current built-in `.state/put` is only a thin wrapper around the `put`
action. It covers direct public writes, but it does not cover all automatic
stub creation paths.

### Stub creation is not one path

Today stub creation happens in multiple places:

- `extract_links` emits `put_item` mutations for some discovered targets
- edge-tag processing creates missing targets directly in core code
- `_restore_current_edges_without_backfill` creates missing targets during
  migration/recovery
- edge backfill creates missing targets in background processing

There is also a related but separate helper path:

- `_ensure_inverse_tagdoc` creates inverse `.tag/*` system docs with
  `_source=auto-vivify`

That system-doc helper should remain outside `.state/stub`. It is not target
materialization from user or content references; it is system-doc maintenance.

If policy only lives on direct `put`, these paths diverge immediately.

## Design Summary

Split the design into two distinct layers:

- a general assessment step that every relevant ingestion/materialization path
  can invoke
- a first concrete URL-specific assessment implementation backed by
  VirusTotal

The general step is the architecture. VirusTotal is only the first override
implementation that plugs into that architecture.

Introduce a shared assessment flow and make both built-in `put` and built-in
`stub` call it. Extracted external URLs discovered inside content must also
enter the same architecture via the stub path, while preserving their existing
`_source=link` provenance.

State layout:

- `.state/put`
  - built-in caller flow for normal writes
- `.state/stub`
  - built-in caller flow for insert-if-absent stub creation
- `.state/assess`
  - built-in general assessment flow that returns directives, not storage side
    effects
- `.state/assess/virustotal`
  - the first URL-specific assessment implementation

Key rules:

- `put` and `stub` always call `assess`
- extracted external URLs found in content are routed into `stub`, so they also
  call `assess`
- `assess` decides the verdict and returns directives
- `put` and `stub` remain responsible for their own write semantics

This keeps policy centralized while preserving the different storage semantics
of normal writes versus stub creation.

## Naming

Use `stub` as the state name rather than `autovivify`.

Reasons:

- shorter and easier to type
- closer to user-facing terminology
- describes the result, not the mechanism

This does not require changing the existing `_source=auto-vivify` data tag
immediately. The state name and the provenance tag can diverge for now.

## Responsibilities

### `.state/assess`

This is the general assessment step. It is a decision flow, not a write flow.
It should not perform final writes.

Its job is to:

- inspect the target identity and source context
- decide which assessment implementations apply
- aggregate results from provider-specific or target-specific fragments
- return a normalized assessment result to the caller

Its default built-in behavior should be a no-op assessment, such as:

```yaml
assessment: clean
summary: ""
stop_processing: false
skip_fetch: false
extra_tags: {}
body: ""
```

Fragments under `.state/assess/*` refine this result.

The purpose of the general step is to cover all relevant paths through one
interface, even when different target kinds eventually need different concrete
assessment systems.

### `.state/put`

This is the normal write caller.

It always invokes `assess`, then:

- if malicious:
  - do not fetch remote content
  - write explanatory content/summary
  - tag `assessment: malicious`
  - suppress further processing
- if suspicious:
  - tag `assessment: suspicious`
  - write the normal content or fetch/store the normal URI body
  - continue normal processing
- otherwise:
  - perform the normal write unchanged

### `.state/stub`

This is the stub-creation caller.

It always invokes `assess`, then:

- if malicious:
  - create or preserve a stub with explanatory content if absent
  - tag `assessment: malicious`
  - suppress further processing
- if suspicious:
  - create the normal stub if absent
  - tag `assessment: suspicious`
  - continue normal stub processing
- otherwise:
  - perform normal stub creation unchanged

Unlike `put`, `stub` must preserve insert-if-absent semantics and must never
overwrite an existing real note.

It is also the point where extracted targets from existing content enter the
assessment architecture. A URL discovered by link extraction is not assessed in
the extraction action itself; it is assessed when the extracted target is
materialized via `stub`.

The stub caller must preserve provenance. In particular:

- extracted-link callers keep `_source=link`
- other automatic stub callers default to `_source=auto-vivify`

This avoids changing current behavior that distinguishes link-created stubs
from other stub notes.

## Why `assess` Returns Directives Instead Of Writing Notes

`put` and `stub` have different semantics:

- `put` may fetch a URI or store inline content
- `put` may preserve a caller-provided custom ID while assessing a URI
- `stub` must be atomic insert-if-absent
- `stub` must not clobber existing content

If `assess` performed writes directly, it would need to know too much about
these caller-specific rules. That would tightly couple policy to storage and
make reuse brittle.

Instead, `assess` should return directives like:

```yaml
assessment: malicious | suspicious | clean | disabled | unknown
summary: "short explanation"
body: "replacement or explanatory content when needed"
extra_tags:
  assessment: suspicious
stop_processing: true | false
skip_fetch: true | false
```

Then `put` and `stub` carry those directives out in their own native way.

## General Assessment Step

The general step exists to standardize assessment across all relevant paths,
not only direct URL puts.

Every caller should present the same conceptual input to `assess`:

- what target is being considered
- what kind of target it is
- which path is invoking assessment
- any caller context needed for policy

This keeps future assessors from needing bespoke integration per caller.

## Assessable Target Resolution

Both callers should compute a target description before invoking `assess`.

For `put`:

- if `params.uri` is `http(s)`, assess that URI
- otherwise if `params.id` is an `http(s)` URL, assess that ID
- otherwise assessment may still apply for other target types later, such as
  email addresses

For `stub`:

- assess the target ID being materialized
- classify the target kind from the ID shape

Initial kinds:

- `url`
- `email`
- `generic`

This allows future fragments beyond URL reputation.

Additional kinds may include:

- `domain`
- `person`
- `file`

The core design should not assume only URL assessment exists.

## VirusTotal As The First Concrete Assessment Implementation

`.state/assess/virustotal` is an optional policy fragment for URL targets.

It is the first concrete override-implementation for the general assessment
step, not a special-case architecture.

It should:

- only apply to `kind == url`
- only perform lookups when a VT API key is present
- use lookup-only behavior
- not auto-submit unknown URLs for scanning

Rationale:

- lookup-only avoids disclosing arbitrary URLs to a third-party service as a
  side effect of normal note ingestion
- this is especially important for links extracted from private mail or user
  documents

Expected policy:

- `malicious > 0`
  - `assessment: malicious`
  - `stop_processing: true`
  - `skip_fetch: true`
- else if `suspicious > 0`
  - `assessment: suspicious`
  - `stop_processing: false`
- else
  - `assessment: clean`

If the API key is absent:

- `assessment: disabled`
- no tags added
- caller proceeds normally

This keeps VirusTotal optional and prevents the general assessment step from
being synonymous with VirusTotal.

The VirusTotal-specific code should live in its own action, for example:

- `keep/actions/assess_virustotal.py`

The action is responsible for VT-specific request/response handling and for
mapping the result into the normalized assessment directive shape. The
state-doc layer is responsible for deciding when that action runs.

The VT action must also handle operational concerns:

- cache results by normalized assessment target and provider
- apply a TTL so repeated writes do not re-query immediately
- fail open on timeout, rate-limit, or service-unreachable conditions by
  returning `assessment: unknown`

Bulk imports or documents containing many extracted URLs will otherwise block on
provider latency and exhaust low external rate limits quickly.

## Path Coverage

The goal of the general assessment step is to cover all relevant paths in one
way.

Initial paths:

- direct `put(content=...)`
- direct `put(uri=...)`
- direct `put(uri=..., id=custom)`
- extracted external URLs discovered inside content and materialized as target
  notes
- edge-tag target materialization
- migration/recovery target materialization via
  `_restore_current_edges_without_backfill`
- edge-backfill target materialization

Excluded path:

- inverse `.tag/*` system-doc creation via `_ensure_inverse_tagdoc`

The architecture should make these look like different callers of one
assessment step, not like separate policy systems.

## State-Doc Subflow Syntax

The clean way for one state doc to invoke another is explicit subflow syntax in
`do:`.

Proposed form:

```yaml
- id: assessed
  do: .state/assess
  with:
    target_id: "{params.id}"
    target_uri: "{params.uri}"
    source: "put"
```

Semantics:

- if `do:` names an action, dispatch to the action system as today
- if `do:` names `.state/<name>`, run the named state doc as a child flow
- the child flow's returned data becomes the parent rule binding

This is preferable to a bespoke bridge action such as `assess_target`, because
calling another state doc is a general flow-language capability rather than a
domain-specific assessment feature.

The subflow facility should be generic and reusable.

## Built-In `put` Flow Shape

The built-in `.state/put` should include assessment by default.

Conceptually:

1. Resolve the assessment target
2. Run the general assessment step via `do: .state/assess`
3. If malicious:
   store explanatory content, tag malicious, suppress background processing,
   return done
4. If suspicious:
   perform the normal write with extra tags merged in, return done
5. Otherwise:
   perform the normal write path

Important detail: the flow-side `put` action must accept
`queue_background_tasks: false`. Most of the lower-level plumbing already
exists; this is mainly action/context parameter threading. Without it, a
malicious note cannot cleanly stop downstream processing after being stored.

## Built-In `stub` Flow Shape

The built-in `.state/stub` should be the canonical entry point for all
automatic stub creation.

Conceptually:

1. Resolve the target ID and kind
2. Run the general assessment step via `do: .state/assess`
3. If malicious:
   create an assessed stub if absent, suppress background processing, return
4. If suspicious:
   create a normal stub if absent, merge suspicious tag, continue
5. Otherwise:
   create the normal stub if absent

Crucially, the final write step must not be normal `put`. It must be a
dedicated insert-if-absent storage action so the flow preserves and
standardizes stub atomicity.

## Action Boundaries

We need one new write action and one VT-specific action.

### `assess_virustotal`

Purpose:

- perform VirusTotal-specific assessment work
- map VT responses into normalized assessment directives

Inputs:

- `target_uri`
- optional future file hash inputs for file-hash lookup

Output:

- normalized assessment directive object

### `stub`

Purpose:

- atomically create a stub note if absent
- preserve caller-specified stub provenance
- optionally apply assessment-derived tags/body/summary

Inputs:

- `id`
- `content`
- `summary`
- `tags`
- `created_at`
- `queue_background_tasks`

Behavior:

- if target exists, do not overwrite it
- if target does not exist, create it atomically

Default provenance:

- if the caller does not supply an explicit source tag, set
  `_source=auto-vivify`
- if the caller supplies `_source=link`, preserve it

This keeps current link-extraction provenance intact while still routing all
stub creation through one write primitive.

This action is the write primitive used by `.state/stub`.

## Runtime Support Required For Subflows

`do: .state/foo` is a syntax and runtime feature, not just documentation.

Required support:

- validation must accept `.state/*` targets in `do:`
- runtime dispatch must distinguish between actions and child state docs
- child flow return data must be exposed as the parent rule binding

Status mapping must be defined carefully:

- child `done`:
  bind returned data normally
- child `error`:
  fail the parent flow
- child `async` or `stopped`:
  either disallow in this context or define explicit resume semantics

For `assess`, the intended use is synchronous and bounded. `put` and `stub`
should not continue until assessment has completed.

## Routing All Stub Creation Through `.state/stub`

All relevant automatic stub creation paths should converge on the new built-in
`.state/stub` flow.

That includes:

- link extraction auto-created targets
- edge-tag target materialization
- migration/recovery target materialization
- edge-backfill target materialization

It explicitly excludes:

- inverse `.tag/*` system-doc creation

This is the right abstraction boundary because:

- URLs, email addresses, people, files, and other referenced entities are all
  target-note materialization events
- the general assessment step is meant to cover these target-note
  materialization paths uniformly
- future reputation or enrichment policies will likely apply to several of
  these target kinds
- it centralizes policy without redefining all mutation-created writes

This should not be generalized to every `put_item` mutation.

Why not:

- non-stub derived writes such as analyzer parts are not policy targets
- broad rerouting would change the semantics of unrelated background writes
- it would make `.state/put` an accidental policy hook for all derived notes

The boundary should be: all relevant stub creation through flow, not all
mutation writes through flow.

One intentional benefit of this convergence is that the new `stub` action uses
`insert_if_absent` uniformly. This replaces the current divergence where some
stub paths are atomic and the edge-backfill path is a check-then-write.

## Interaction With Existing Private/Internal URL Blocking

No change.

The HTTP document provider already blocks private/internal URL fetches. That is
a transport/network guard and should remain in place regardless of assessment.

The new assessment system is additive:

- network guard blocks unsafe fetch destinations
- assessment flow applies content/reputation policy on assessable targets

These solve different problems and should not be merged.

## Custom IDs For `put(uri=..., id=...)`

When the caller provides a custom ID, preserve it.

Assessment still targets the URI, but the stored note identity remains the
caller-specified ID.

Implications:

- the assessment directive should carry both target URI and effective note ID
- malicious explanatory writes should still use the caller's custom ID
- tags and summary must describe the assessed URI

## Reassessment Semantics

Assessment tags describe the current assessment result for the current write
target, not permanent historical status.

Rules:

- every `put` and every `stub` invocation runs assessment for its current
  target description
- if a new applicable assessment returns `clean`, the caller clears prior
  assessment tags
- if a write no longer presents an assessable target, the caller clears prior
  assessment tags rather than preserving stale security state

This ensures `assessment:*` reflects the latest applicable result rather than
becoming sticky metadata.

## Summary/Body Rules

For malicious results:

- write explanatory content even for direct URI puts
- summary should be a short explanation such as:
  `VirusTotal: malicious (3 malicious detections)`
- body should record what was assessed and why processing stopped

For suspicious results:

- preserve normal content semantics
- summary remains whatever the normal write path would use, unless the caller
  explicitly wants the assessment summary surfaced there later

This keeps suspicious results lightweight while making malicious results
visible and self-explanatory.

## Background Processing Semantics

Malicious:

- no remote fetch
- no `after-write`
- no summarization/analyze/describe/link extraction triggered afterward

Suspicious:

- normal processing continues
- the note simply carries `assessment: suspicious`

The simplest implementation is:

- malicious callers write with `queue_background_tasks: false`
- suspicious and clean callers keep default behavior

It may still be worth adding an early `after-write` guard for
`assessment: malicious` as defense in depth, but the primary stop should happen
in the caller flow.

## Concrete Separation Of Concerns

### General assessment step

The general step provides:

- one entry point
- one normalized result shape
- one place to aggregate multiple assessors
- one contract shared by `put` and `stub`

It should know about:

- target classification
- caller/source context
- result normalization
- verdict precedence

It should not know about:

- direct note storage semantics
- insert-if-absent semantics
- caller-specific write details

### VirusTotal URL assessment

The VirusTotal-specific layer provides:

- URL-only applicability
- VT request/response handling
- mapping VT results into normalized assessment directives

It is just one implementation hanging off the general step.

That means future work can add siblings such as:

- `.state/assess/email-reputation`
- `.state/assess/domain-policy`
- `.state/assess/local-denylist`

without changing the caller flows.

## Open Questions

### Aggregation across multiple assessors

Today VirusTotal is the first implementation. Later we may have:

- URL reputation
- email-address reputation
- domain allow/block policy
- local allowlists or denylists

The shared general assessment step should be designed so multiple fragments can
contribute results without ambiguous precedence. The simplest rule is:

- `malicious` dominates `suspicious`
- `suspicious` dominates `clean`
- `disabled` and `unknown` are informational only

### Exact tag schema

Initial tag requirement is only:

- `assessment: malicious|suspicious`

We will likely also want:

- `assessment_provider: virustotal`
- `assessment_checked_at: ...`

Those can be added without changing the overall flow design.

## Implementation Plan

1. Add generic subflow support so state docs can use `do: .state/foo`.
2. Add built-in `.state/assess` as the general assessment step, with a no-op
   default result.
3. Add runtime tests for child-flow return binding, error propagation, and
   forbidden async/stopped behavior before assessment-specific work begins.
4. Wire `queue_background_tasks` through the flow-side `put` action/context.
5. Add built-in `.state/stub`.
6. Add `stub` action with atomic insert-if-absent semantics and caller-supplied
   provenance.
7. Route one existing stub path through `.state/stub` while `.state/assess`
   still returns no-op, and verify the existing test suite still passes.
8. Change the remaining relevant stub-creation paths, including extracted
   external URLs, to route through `.state/stub`.
9. Add `assess_virustotal` action with caching, timeout, and rate-limit
   fallback.
10. Add `.state/assess/virustotal` as the first concrete URL assessment
   implementation.
11. Add tests covering:
   - direct `put(uri=...)` malicious
   - direct `put(uri=...)` suspicious
   - direct `put(uri=..., id=custom)` malicious
   - stub URL malicious
   - stub URL suspicious
   - stub email unchanged when no assessor applies
   - existing real note not overwritten by stub flow
   - extracted-link stubs preserve `_source=link`
   - system inverse tag docs do not route through `.state/stub`
   - VT timeout/rate-limit returns `unknown` and allows the write to proceed

## Conditions Of Satisfaction

- Direct `put`, extracted external URLs, and automatic stub creation share one
  general assessment step.
- VirusTotal behavior is implemented once, under `.state/assess/virustotal`,
  as the first concrete URL-specific implementation.
- `put` and `stub` always invoke the general assessment step.
- the general assessment step is invoked through state-doc subflow syntax,
  `do: .state/assess`.
- `assess` returns directives rather than performing final writes.
- Malicious URLs become explanatory notes tagged `assessment: malicious` and do
  not continue processing.
- Suspicious URLs are tagged `assessment: suspicious` and otherwise proceed
  normally.
- Extracted-link stubs keep `_source=link`; other stub paths keep
  `_source=auto-vivify` unless they explicitly opt into different provenance.
- Stub creation remains atomic and does not overwrite existing notes.
- Inverse `.tag/*` system-doc creation remains outside `.state/stub`.
- Existing private/internal URL blocking remains unchanged.
