# Decision Support As Iterative Reconstruction Over Memory Space

Date: 2026-04-02
Status: Draft

## Summary

This note updates the design direction for `deep`, query-time support,
and future reconstruction work.

The current conclusion is:

- keep should not be designed around a fixed read result shape
- keep should not treat retrieval channels as the primary semantics
- keep should not begin from a standalone reranker design
- keep should instead treat read-time support as **iterative
  reconstruction** over a hidden memory basis
- keep should keep content embeddings focused on text retrieval, while
  leaving open a second structural space for graph / lineage / tag /
  part geometry if later experiments justify it

In this view:

- embeddings
- full-text search
- tags
- edges
- temporal order
- version relationships
- parts

are measurement operators over the same memory domain.

But they are not all measurements in the same space. The current
uncommitted embedding changes already strengthen the content-embedding
path by distinguishing `document` from `query` tasks for asymmetric
models. That is good for semantic retrieval, and it argues against
forcing structural and temporal relations into the same content space.

The read path then becomes:

1. gather partial measurements
2. broaden support locally from promising handles
3. merge those local supports coverage-first
4. stop when the support is sufficient for the current act

This is already closer to what `deep` does than to what a classic
reranker does.

The design goal is therefore:

- make the bundled pieces of `deep` composable
- let flows choose reconstruction recipes
- let generic runtime operators perform bounded expansion and merge
- support multiple use cases without fixing one universal output schema

## Why This Matters

Today keep already has effective read-time behavior, but the pieces are
still organized around visible retrieval features and legacy section
shapes:

- `similar`
- `parts`
- `prev` / `next`
- `meta/*`
- deep edge-follow groups
- prompt-time context assembly

Those are useful, but they do not yet express the real task.

The real task is not:

- find more notes
- return top-k notes
- attach a few context sections

The real task is:

- determine which organizing dimensions matter for the concern
- gather evidence across those dimensions
- recover a small answer-bearing support/path
- present that support so an agent can answer or act skillfully

That is decision support in the stronger sense: not option ranking, but
reconstruction of the situation that matters.

## Research Grounding

This framing fits the Winograd / Flores / Varela lineage.

### Flores and decision support

Flores and Ludlow argue that decision support should begin from the
breakdown in ongoing work. The system matters insofar as it helps
resolve commitments, clarify conditions of satisfaction, and support
action in context.

For keep, that means read-time support is not merely retrieval. It is
support for resolving a situated concern.

### Winograd and language/action

Winograd's language/action perspective says the primary design question
is not how to represent information in the abstract, but how a system
supports requests, offers, assertions, assessments, coordination, and
breakdown recovery.

For keep, the read path should therefore help reconstruct a practical
and conversational situation, not merely return a ranked list.

### Varela and skillful coping

Varela emphasizes situated discrimination and skillful action rather
than detached rule application.

For keep, the relevant questions are often:

- what is going on?
- what changed?
- what matters now?
- what move would be skillful next?

The read path should therefore provide orientation, not only explicit
explanation.

### XState and LambdaMOO

XState provides the execution model:

- sequencing
- branching
- guards
- actors
- bounded multi-step work

But XState does not itself provide the language of the domain.

LambdaMOO remains useful for two reasons.

First, it offers spatial and dynamical analogies for moving through a
domain:

- rooms
- exits
- containment
- neighborhoods

Second, it offers an execution model for live objects with verbs and
message dispatch:

- objects are active runtime entities
- the player object provides perspective and current position
- verbs define possible acts
- `look` is the universal act for orientation

That maps well onto keep:

- notes, supernodes, versions, and parts are the runtime objects
- state docs define verb-like behavior
- meta-docs contribute situated perception
- `now` acts like a player perspective
- `get`, `find`, `deep`, and future reconstruction recipes are all
  specialized forms of `look`

So the missing piece is not only a query language. It is a language for
dispatching reconstruction verbs over a live memory space.

### Compressive sensing and representative selection

Compressive sensing gives a useful discipline for thinking about this
problem.

The system never sees the whole situation directly. It sees samples:

- query hits
- graph neighbors
- tag matches
- versions
- parts
- local windows

These are measurements. The support needed for a concern is often much
smaller than the corpus.

So the read problem can be restated as:

- choose the basis families in which the concern is sparse
- combine heterogeneous measurements
- recover the small support/path that best explains the concern

This is not only metaphor.

The recent spike work suggests a real design pattern:

- broadening from a few good handles is often better than re-scoring a
  flat top-k
- grouped local support is often a better unit than isolated notes
- the problem is closer to representative support selection than to
  ordinary reranking

The Elhamifar / Sapiro / Vidal line of work is relevant here because it
frames representative subset selection directly as sparse recovery and,
in the dissimilarity-based formulation, does not require one canonical
embedding space. That matches keep better than a single-vector-space
story.

## Information-Theoretic Restatement

Given:

- a corpus `D` of notes
- a question or concern `q`
- multiple relationship domains over `D`

the task is not to produce an answer token directly.

The task is to recover a small structured support/path `S_q ⊂ D` such
that an agent conditioned on `q + S_q` can answer or act nearly as well
as if it had access to the full relevant corpus.

In this formulation:

- `q` is a projection request over several basis dimensions
- each retrieval channel yields partial measurements in one or more
  dimensions
- reconstruction seeks a support/path jointly coherent across those
  dimensions

The key object is therefore not `answer(q)`, but the
**answer-bearing support/path**.

That unifies the main use cases:

- factual QA
  - support/path from which the answer can be derived
- research
  - support/path from which inquiry can continue fruitfully
- conversational or action support
  - support/path from which the next move can be skillful

## What Is The Hidden Basis?

The hidden basis is not one thing. It is a family of organizing
dimensions already present in notes.

At minimum:

- semantic topic and neighborhood
- conversation and speech act
- commitments and open-loop state
- temporal position
- version lineage and change
- structural decomposition into parts
- explicit graph relationships
- learned patterns and meta guidance

These are not retrieval methods. They are basis families in which a
situation may become sparse and intelligible.

The retrieval methods are just probes:

- embeddings probe semantic neighborhood
- FTS probes lexical surface and named anchors
- tags probe declared or derived facets
- traversal probes explicit local topology
- version navigation probes lineage
- part analysis exposes internal structure to the same navigation
  machinery

That last point matters: analysis parts are not only a presentation
trick. They pave otherwise hidden structure into a form that becomes
legible to navigation and reconstruction.

## What The Spike Has Established

The offline spike has moved the design from conjecture to a more
concrete direction.

### 1. `towards` and `around` are distinct stances

Two geometric stances are needed:

- `towards`
  - recover support/path from the query position toward an
    answer-bearing region
- `around`
  - recover the local neighborhood that makes the concern intelligible

This is not only a presentation distinction. It affects the
reconstruction method.

### 2. Plain greedy directional recovery works better for `towards`

On self-supervised probes, sparse directional recovery works
reasonably well. That suggests the domain does admit recoverable sparse
support in at least one important regime.

### 3. `around` improves when decomposed into local expansions plus merge

The strongest result for `around` so far is not a reranker and not a
flat grouped selector. It is:

1. propose a few handles
2. broaden locally from each
3. merge coverage-first

In the LoCoMo `around` slice, this `multi_towards` decomposition
approached deep's strict recall while keeping support tiny, and on the
larger benchmark run it reached `0.600 / 0.550` strict hit / recall.

### 4. Iteration has measurable value

The handle sweep showed:

- one directional handle gives the single-path baseline
- the second handle helps a little
- the third handle produces the main gain
- the fourth handle adds little or nothing

So the practical pattern is:

- directional breadth matters more than per-handle depth

### 5. Benchmark and main-store results now diverge in a useful way

On the benchmark side, `multi_towards` has become the strongest sparse
`around` method so far. On the informal `~/.keep` checks, grouped local
support still transfers better for open, messy free-text concerns.

That suggests a more precise reading:

- `multi_towards` works best when the concern gives strong handles and
  the task decomposes cleanly into a few directional subproblems
- grouped local broadening is still the safer generic default when the
  concern does not expose strong handles or the store is diverse and
  messy

This is why the composition modes should remain explicit. The choice is
not "grouped or directional forever," but which reconstruction geometry
fits the current concern.

### 6. Global embedding basis pursuit is not working

The embedding experiments have now ruled out one tempting simplification:
pure embedding OMP over the whole candidate pool.

On both LoCoMo and `~/.keep`, whole-pool embedding OMP is materially
worse than the current mixed-feature reconstruction paths. The candidate
pools remain high-rank and diffuse, so global basis pursuit in content
embedding space does not recover useful support reliably.

### 7. Local structural neighborhoods do have embedding structure

The same experiments show a stronger local result:

- `base-local`
- `lineage-local`
- `part-window`

groups are much more compressible in embedding space than the whole
pool, while `edge-local` groups remain relatively weak and diffuse.

This suggests that any future embedding-based reconstruction should be
constrained inside structural neighborhoods chosen by ordinary
measurement signals, not used as a standalone global selector.

### 8. Non-oracle local-group selection from embedding compactness is weak

Two non-oracle selectors were tried for local embedding reconstruction:

- a compactness-first selector
- a support-seeded selector anchored on flat mixed-feature support

The compactness-first selector failed badly by choosing irrelevant but
highly self-similar lineages. The support-seeded selector was more
reasonable, but it still underperformed simply taking the top measured
local group.

So the current practical reading is:

- local embedding geometry is real
- but group choice should still be driven mainly by ordinary retrieval
  measurement, not by embedding compactness alone

## Design Direction

The current design direction is:

- do not start from a reranker
- do not start from a fixed result schema
- start from a generic reconstruction loop built from measurement,
  broadening, merge, and sufficiency

`deep` should then be understood as an early bundled recipe over that
substrate.

## Composition Model

The right composition model is not "retrieve, then maybe rerank."

It is an iterative reconstruction program.

### 1. Observe

Start from a concern:

- a user query
- the current turn
- a focal note
- changed notes
- a candidate relationship

### 2. Measure

Collect partial measurements across active basis families:

- semantic
- lexical
- structural
- graph
- temporal
- tags
- meta / commitment / conversation when relevant

### 3. Propose handles or groups

Produce a small set of local starting points.

These remain generic:

- a note
- a version lineage
- a part window
- an edge-local neighborhood
- a supernode
- a tag-induced neighborhood

Speaker-centered support is not a separate primitive. It is one kind of
edge-local neighborhood around a supernode.

### 4. Broaden locally

From each handle or selected local group, broaden support along generic
operators:

- versions near a base
- adjacent parts
- local edge neighborhood
- tag-local neighbors
- temporally ordered local neighbors inside an already meaningful local
  structure

This is the step that should replace the idea of a standalone
reranker. The main design act is selective broadening, not only
reordering.

### 5. Merge coverage-first

Merge local supports so the result does not collapse into one dominant
cluster.

This can reward:

- new facet coverage
- new anchor coverage
- new local region coverage

while penalizing:

- redundant support from the same cluster
- pure hubness
- decorative notes that add no new explanatory power

### 6. Test sufficiency

At each stage, ask:

- is enough of the concern now explained?
- is the remaining signal concentrated or diffuse?
- should another handle be explored?
- should the process stop and present ambiguity?

### 7. Present for action

Presentation remains flow-defined and use-case-specific.

The design should not standardize one universal result envelope now.

## Concrete Composition Modes

The implementation should support a small number of composition modes
rather than one universal reconstruction strategy.

### Mode 1. Direct local support

Use when:

- one dominant region is likely
- ambiguity is low
- the concern is narrow

Shape:

- measure
- take strongest local support
- broaden once
- stop

This is the lightest path.

### Mode 2. Directional recovery (`towards`)

Use when:

- the concern has a clear target or orientation
- one answer-bearing region is likely
- lineage, path, or explicit relation matters

Shape:

- measure
- choose one directional handle
- broaden locally toward that region
- stop when residual drops

This is where the current greedy sparse loop works best.

### Mode 3. Grouped local support (`around`)

Use when:

- the concern is neighborhood-oriented
- a single best note is not enough
- local coherence matters more than path-following

Shape:

- measure
- construct local groups
- select groups by coverage and coherence
- emit representative support from the selected groups

This is the safer generic default for diverse stores.

### Mode 4. Multi-handle directional broadening

Use when:

- the concern can be decomposed into a few directional subproblems
- explicit or inferred handles are available
- the goal is to recover several local supports and merge them

Shape:

- propose a few handles
- run shallow `towards` expansions from each
- merge coverage-first

Current evidence suggests:

- around three handles is usually enough
- per-handle depth should stay shallow
- this works best when handle quality is strong

### Mode 5. Deep compatibility recipe

Use when:

- broad recall matters more than precision
- uncertainty remains high after cheaper passes
- compatibility with today's deep behavior is desired

Shape:

- broad initial measurement
- local edge/structure expansion
- grouped evidence windows
- coverage-first support allocation

This is not a separate conceptual feature. It is one heavy recipe over
the same substrate.

## Where The Logic Should Live

The practical split should be hybrid.

Flows should do the strategic work:

- choose basis families
- choose a composition mode
- set budgets
- choose widening or stopping behavior
- decide when to escalate

Runtime operators should do the tactical work:

- collect measurements
- build bounded candidate pools
- broaden locally
- merge supports
- compute diagnostics and sufficiency signals

This preserves the value of flows without forcing the flow language to
encode every inner scoring loop directly.

## How This Maps To Current Code

### `deep`

`deep` already contains most of the heavy recipe:

- initial sample
- local expansion
- `EvidenceUnit` creation
- `ContextWindow` grouping
- rescoring
- dedup
- per-source caps
- coverage-first allocation

So the refactoring target is not a new feature beside `deep`.
It is:

- split `deep` into reusable moves
- let flows compose those moves
- keep `deep` as one compatibility recipe

### Reconstruction spike

The spike now supports three relevant reconstruction shapes:

- flat sparse selection
- grouped support selection
- multi-handle directional expansion plus merge

The results suggest:

- grouped support should remain a first-class composition mode
- multi-handle directional expansion should be optional, not universal
- the system should prefer broadening and merge over a standalone
  reranker concept

## Use Cases

### Conversational turn support

The system should answer:

- what conversation is active?
- what changed?
- what commitments or breakdowns matter?
- what background matters without taking over the turn?
- what would make the next move skillful?

Likely composition:

- grouped local support by default
- multi-handle directional expansion when the turn names specific
  anchors or targets

### Find and research as an agent

The system should answer:

- what hypotheses explain the sample?
- what passages or neighborhoods support them?
- where is ambiguity real?

Likely composition:

- direct local support for simple research queries
- directional recovery when one target region is implied
- grouped local support when several neighborhoods matter

### Background maintenance, tagging, and analysis

The system should answer:

- what local structure justifies a tag or relationship?
- what is inconsistent?
- what should be persisted or reviewed?

Likely composition:

- grouped local support
- directional recovery for lineage or relation repair

## Concrete Implementation Suggestion

The near-term implementation should center on generic read-time
operators:

1. `measure`
   - collect channel evidence into a bounded working pool

2. `broaden`
   - expand locally from a handle or group

3. `merge`
   - combine local supports coverage-first

4. `test`
   - estimate sufficiency and residual

5. `present`
   - expose support for the current flow

These should be parameterized, not hard-coded to one domain.

The key point is:

- a future reranker is at most one internal tactic inside `merge` or
  `broaden`
- it is not the architectural center

## Verification And Investigation Tasks

The design direction is now plausible enough to guide refactoring, but
it still needs more verification.

### Immediate verification

1. confirm transfer on larger `~/.keep` samples
   - especially free-text queries, not only self-supervised probes

2. verify composition-mode choice
   - when should grouped support beat multi-handle directional
     expansion, and vice versa?

3. keep the benchmark accounting clean
   - empty-target rows such as `qa:1821` should remain classified as
     tooling artifacts, not reconstruction misses

4. keep content-embedding improvements and reconstruction work separate
   in evaluation
   - task-aware content embeddings are already improving semantic
     retrieval quality
   - any later structural-space work should be measured in addition to,
     not instead of, those improvements

### Immediate investigation

1. improve within-group emission ordering
   - this is now an observed failure mode, not a hypothesis
   - on `qa:782`, the target versions are present in the right base
     group but rank below the emitted representatives

2. improve generic handle proposal
   - multi-handle directional expansion underperforms when the concern
     does not expose strong anchors

3. investigate remaining pool misses
   - the remaining upstream failure class is candidate-pool coverage
   - at least one real benchmark miss is still a pool miss rather than
     a reconstruction miss
   - if that pattern generalizes, pool construction needs another round
     of targeted broadening

4. reduce repeated directional computation
   - multi-handle recovery still recomputes too much local scoring

5. investigate structure-constrained embedding reconstruction
   - whole-pool embedding OMP is not good enough
   - local embedding reconstruction inside `base-local`,
     `lineage-local`, and `part-window` groups is more promising
   - the next question is whether a product path should use embedding
     reconstruction only after ordinary retrieval has already selected a
     structural neighborhood

6. investigate whether a second structural embedding space is warranted
   - not as a replacement for the content embedding
   - but as a separate graph / lineage / tag / part space if offline
     neighborhood experiments continue to show reusable structure

7. compare grouped support against current `_deep_edge_follow` behavior
   - clarify which existing deep heuristics should become reusable
     generic operators

8. clarify temporal semantics as conditioning, not generic adjacency
   - timestamps alone should not produce global temporal edges
   - version and other explicit local structures already provide the
     trustworthy substrate
   - the next temporal question is how to represent conditional
     relations such as `supersedes`, `responds_to`, `opens_loop`, and
     `satisfies` inside local reconstruction without creating a noisy
     global temporal mesh

### Established findings

1. roughly three handles is the practical sweet spot for
   `multi_towards`
   - shallow depth, broader directional coverage
   - the fourth handle added little or nothing in the current sweep

2. content embeddings should remain task-aware and text-focused
   - asymmetric models benefit from distinguishing document and query
     embeddings
   - this makes it less attractive to overload the content space with
     structural relations

3. global embedding basis pursuit is not the next algorithm
   - the useful embedding structure is local and structural, not
     whole-pool and global

4. temporal semantics should be treated primarily as conditioning, not
   as global temporal adjacency
   - timestamps are useful as local ordering and distance signals
   - explicit structures such as versions remain the trustworthy local
     substrate

### Refactoring tasks

1. expose the reusable pieces of `deep`
   - evidence creation
   - grouping
   - local expansion
   - coverage allocation
   - emission

2. define the runtime operator boundary
   - what flows can call directly
   - what remains internal tactical logic

3. keep the data model generic
   - no speaker-specific, conv-specific, or dataset-specific operator
     names
   - tags and edges remain generic signals with context-dependent
     weight

## Conditions Of Satisfaction

This direction is successful when:

- `deep` is describable as a recipe rather than a special mode
- broadening and merge become the conceptual center of read-time
  support
- flows can choose composition modes without hard-coding provider- or
  dataset-specific logic
- sparse support is recoverable on diverse notes, not only on LoCoMo
- the system helps an agent answer:
  - what is going on?
  - what is changing?
  - what matters now?
  - what action would be skillful next?

## References

- Fernando Flores and Juan J. Ludlow, "Doing and Speaking in the
  Office," in *Decision Support Systems: Issues and Challenges*, 1980.
  https://pure.iiasa.ac.at/1221/1/XB-80-512.pdf
- Terry Winograd, "A Language/Action Perspective on the Design of
  Cooperative Work," *Human-Computer Interaction* 3:1, 1987-88.
  https://hci.stanford.edu/winograd/papers/language-action.html
- Francisco J. Varela, *Ethical Know-How: Action, Wisdom, and
  Cognition*, Stanford University Press, 1999.
  https://www.sup.org/books/theory-and-philosophy/ethical-know-how
- Stately XState actor model docs.
  https://stately.ai/docs/actor-model
- LambdaCore manuals for LambdaMOO spatial and object/verb execution
  metaphors.
  https://brn227.brown.wmich.edu/Barn/files/docs/lambdamoo/LambdaCoreUserMan.html
  https://thxmoo.org/LambdaCoreProgMan-1.3.pdf
- Emmanuel J. Candès and Michael B. Wakin, "An Introduction To
  Compressive Sampling," 2008.
  https://authors.library.caltech.edu/records/epx8s-y1b11/latest
- S. Chen, D. L. Donoho, and M. A. Saunders, "Atomic Decomposition by
  Basis Pursuit," 1995.
  https://statistics.stanford.edu/technical-reports/atomic-decomposition-basis-pursuit
- Ehsan Elhamifar, Guillermo Sapiro, and René Vidal, "See All by
  Looking at a Few: Sparse Modeling for Finding Representative
  Objects," CVPR 2012.
  http://www.vision.jhu.edu/assets/ElhamifarCVPR12.pdf
- Ehsan Elhamifar, Guillermo Sapiro, and S. Shankar Sastry,
  "Dissimilarity-Based Sparse Subset Selection," TPAMI 2016.
  https://people.eecs.berkeley.edu/~sastry/pubs/Pdfs%20of%202015/ElhamifarDissimilarity2016.pdf
