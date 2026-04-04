# Reconstruction Spike Plan

Date: 2026-03-31
Status: Draft

## Goal

Build a small offline spike to test the fundamental hypothesis:

> A sparse explanatory support is discoverable in keep's memory space,
> and that support is more useful than plain top-k retrieval.

The purpose of the spike is not to design the final runtime interface.
It is to determine whether multi-channel reconstruction is real enough
to justify making it a first-class direction.

## Working Hypothesis

The hidden basis of the note collection is structured enough that a
small support set of notes, parts, versions, and neighbors can explain
many concerns better than any single retrieval channel.

More concretely:

1. A multi-channel reconstruction method can recover a useful support
   set with low cardinality.
2. The support set is more informative than semantic top-k or current
   rank fusion alone.
3. Existing statistics such as `margin`, `entropy`, and structural
   density can help choose which reconstruction recipe to run.

## Why `~/.keep` Is Good Enough For The Spike

The live store is large and structurally diverse enough to test the
fundamental idea before building narrower benchmark datasets.

Current local counts:

- ~7,864 non-system notes
- 6,618 versions
- 15,503 parts
- 12,258 edges
- 10,016 version edges

That gives enough density across:

- semantic retrieval
- lexical retrieval
- graph navigation
- structural decomposition
- temporal and version relations

## Proposed Spike Shape

Build one offline harness, not a new runtime subsystem.

The spike should:

1. snapshot a concern or probe
2. collect channel measurements
3. reconstruct a sparse support set
4. record residual and support metrics at each step
5. compare against simple baselines

The right first implementation is a Python benchmark script in `bench/`
that uses existing `Keeper` and database surfaces.

## Candidate Reconstruction Recipe

Orthogonal Matching Pursuit is the best first candidate.

Use it as a concrete reconstruction recipe, not as a premature
commitment to one final model.

Loop:

1. initialize from the concern
2. measure across channels
3. select the strongest next support note
4. update support and residual
5. stop when sufficient or budget exhausted

In keep terms:

- the concern is the signal to explain
- channels produce sparse evidence over notes
- the support is the current explanatory set
- the residual is the unexplained portion of the concern

## What The Spike Needs

### 1. Candidate pool construction

Do not score the full store at every step.

Build a bounded candidate pool from the union of:

- embedding top-N
- FTS top-N
- tag-follow expansion
- edge neighbors
- temporal neighbors
- version and part neighbors

This bounded pool is where reconstruction runs.

### 2. Channel score extraction

For each candidate, extract a feature vector from available channels.

Initial channels:

- semantic score
- lexical score
- tag overlap or tag inclusion
- edge adjacency / edge path signal
- recency / temporal proximity
- version / part relation signal

Optional later:

- meta-doc match
- conversation / commitment indicators
- coverage over extracted facets

### 3. Residual implementation

Start cheap. Do not begin with model-judged residuals.

Implement these residuals in order:

1. channel residual
   - strong unselected signal remains in one or more channels
2. coverage residual
   - uncovered concern facets remain
3. projection residual
   - semantic query content still lies outside support span

Defer:

- LLM-judged residual

### 4. Baselines

The spike must compare against simple baselines:

- embedding top-k
- FTS top-k
- current fused search
- current `deep`

If reconstruction does not beat these at similar support size, the
design claim is weak.

## Probe Families

The spike should not begin with hand-labeled QA only. Use
self-supervised probes generated from `~/.keep`.

### A. Version lineage probes

Goal:

- can reconstruction recover the small lineage support relevant to a
  note's change trajectory?

Probe construction:

- sample versioned notes
- use latest summary or part summary as the concern
- target earlier versions and nearby lineage notes as recoverable support

Why this matters:

- version structure is already explicit
- “what changed?” is central to conversational support

### B. Part / structure probes

Goal:

- can reconstruction recover parent + related parts from a partial cue?

Probe construction:

- sample notes with parts
- use one part summary or text slice as the concern
- target parent note and sibling parts

Why this matters:

- parts are the current way narrative structure is made navigable

### C. Edge neighborhood probes

Goal:

- can reconstruction recover graph-local support from one endpoint?

Probe construction:

- sample notes with strong edge neighborhoods
- use one endpoint summary/query cue
- target neighboring support

Why this matters:

- tests whether explicit relation structure participates in sparse support

### D. Commitment / meta probes

Goal:

- can reconstruction recover open loops and guidance notes from a live
  concern?

Probe construction:

- sample notes that resolve into `meta/todo` or `meta/learnings`
- use focal note or recent-turn cue
- target open-loop and guidance support

Why this matters:

- tests the decision-support thesis rather than only topic retrieval

### E. Query paraphrase probes

Goal:

- is the support stable across paraphrases of the same concern?

Probe construction:

- use stored note summaries, titles, tags, and local paraphrase variants
- compare support overlap and residual curves

Why this matters:

- sparse support should be more stable than raw ranking noise

## Success Metrics

The spike should measure:

### Retrieval usefulness

- answer-in-support or target-in-support
- support recall against probe target set

### Sparsity

- support size
- compression ratio versus raw candidate pool

### Efficiency

- reconstruction steps taken
- wall-clock runtime
- candidate pool size

### Residual behavior

- did residual decrease monotonically?
- where did it stall?
- which channels carried remaining residual?

### Stability

- support overlap across paraphrases or equivalent concerns

## Method Selection Hypothesis

A cheap probe step should predict which recipe to run.

Candidate selection signals:

- high semantic margin, low entropy
  - light semantic-first reconstruction
- low margin, high entropy
  - heavier multi-channel reconstruction
- strong edge density
  - edge-dominant reconstruction
- strong tag overlap, weak edges
  - tag-dominant reconstruction
- high recency density
  - temporal-first reconstruction
- visible open commitments
  - commitment-first reconstruction

This is worth testing even in the spike.

If these signals fail to separate cases, the flow-level recipe
selection idea weakens.

## Likely Implementation Split

For the spike:

- method selection can live in simple Python control logic
- iterative reconstruction should also live in Python
- flow integration is not required to test the hypothesis

For later productization:

- flows should choose recipes and stopping policies
- a reconstruction action should execute the inner iterative loop

This is the likely long-term hybrid split.

## Minimum Deliverable

The minimum convincing spike is:

1. one benchmark script, probably `bench/reconstruct_spike.py`
2. one bounded candidate-pool builder
3. one OMP-like loop with channel residual and coverage residual
4. three probe families:
   - version
   - part
   - edge
5. baseline comparison against semantic top-k, fused search, and `deep`
6. CSV or JSON output showing support size, residual, and recall

## Falsifiers

This direction is weakened if:

- support sets are not meaningfully sparser than top-k
- multi-channel reconstruction does not outperform simple baselines
- residuals do not correlate with usefulness
- support is unstable across equivalent concerns
- candidate pools have to grow so large that reconstruction loses its
  practical advantage

## Current Status

Date: 2026-04-01

The spike has now advanced beyond the original minimum deliverable.

Implemented in [bench/reconstruct_spike.py](/Users/hugh/play/keep/bench/reconstruct_spike.py):

- self-supervised `version`, `part`, and `edge` probes
- LoCoMo QA probes with explicit `around` / `towards` stance
- flat greedy reconstruction
- grouped `around` reconstruction
- oracle upper-bound diagnostics
  - target in pool
  - target in any group
  - target in any emitted representative set
  - deep-only support outside the bounded pool
- internal candidate-pool timing and count diagnostics

Companion analyzer:

- [bench/analyze_reconstruct_run.py](/Users/hugh/play/keep/bench/analyze_reconstruct_run.py)

Most recent `around` QA result:

- run file: [/tmp/reconstruct-spike-qa-around-run4.json](/private/tmp/reconstruct-spike-qa-around-run4.json)
- analyzer command:
  `python bench/analyze_reconstruct_run.py /tmp/reconstruct-spike-qa-around-run4.json`

Current findings:

- grouped pursuit is fast enough to be a viable read-path direction
  - `reconstruct_grouped` mean runtime: `35.67ms`
- flat reconstruction is still much slower than grouped
  - `reconstruct` mean runtime: `5353.35ms`
- deep still has the highest `around` recall, but it gets it by returning
  much larger support sets
  - `deep` base recall: `0.850`
  - mean support size: `67.90`

Most important recent change:

- candidate-pool construction now uses batch embedding fetch and
  anchor-driven scoped FTS probing over anchor-reachable base notes

That changed the spike in two useful ways:

1. Pool cost dropped sharply.
   - earlier timed smoke: candidate-pool mean runtime about `10.1s`
   - current run4: candidate-pool mean runtime about `0.49s`

2. Strict misses are no longer dominated by “target not in pool”.
   - flat `reconstruct` strict miss split on run4:
     - `hit=3`
     - `missing_from_pool=2`
     - `missing_from_groups=3`
     - `selection_miss=2`

That is an important transition. The spike is no longer blocked mainly
by raw I/O cost or by a completely inadequate bounded pool. The next
bottlenecks are:

- grouped neighborhood induction
  - some exact target turns are present in the pool but not present in
    any generated group
- selection within the induced groups
  - some targets are in the grouped search space but still not selected

What remains unresolved:

- `around` strict recall is still low relative to `deep`
- grouped pursuit remains fast but has not yet improved recall over the
  flat loop on the LoCoMo `around` slice
- the pool still misses some exact answer-bearing turns

## Next Step

Continue the offline spike before changing the product runtime.

The next concrete steps are:

1. inspect the `missing_from_groups` strict failures on run4
2. improve group induction so exact version turns present in the pool are
   induced into useful local groups
3. inspect the remaining `selection_miss` strict failures
4. run a separate `towards` batch so `around` and `towards` do not blur
   together in evaluation
5. only then enlarge the LoCoMo sample further
