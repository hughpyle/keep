# Git HEAD Watches And Archived-Hash Restores

Date: 2026-03-30
Status: Draft

Related:
- [watches.md](/Users/hugh/play/keep/later/design/watches.md)
- [git-changelog-ingest.md](/Users/hugh/play/keep/later/design/git-changelog-ingest.md)

## Problem

Two separate gaps show up when watched repositories move through history:

1. Directory watches notice working-tree file changes, but they do not
   notice git-only state changes such as a new commit, an empty commit,
   or a checkout to an older commit or branch. The current directory
   fingerprint is based on visible file paths plus mtimes, not git
   metadata.
2. When a file's content returns to a previously-seen version, keep
   treats it as an ordinary content change relative to the current head.
   That preserves history, but it does not recognize that the content
   already exists in the note's archive. As a result, content-derived
   processing may run again even though the restored content is already
   known.

The user-visible failure mode is not data loss. It is wasted work and
missed git changelog events.

## Goals

- detect commit/check-out events for watched git repositories without
  recursively watching `.git`
- keep git changelog ingest incremental and low-noise
- recognize when new head content matches an archived version of the
  same note
- preserve the current linear version model
- enable selective reuse of prior derived artifacts when that reuse is
  actually safe

## Non-goals

- watching tag-only git events
- treating version history as a branch graph with first-class "future"
  versions
- changing `revert()` semantics
- deleting or rewriting archived versions when a prior content hash
  reappears

## Current Behavior

### Watches

Directory watches hash the working tree, not repo state. The watched
file list comes from the normal directory walker, which respects
`.gitignore` and skips hidden paths. That means `.git` is out of scope
for change detection.

This is good for noise suppression, but it means commit-only events are
invisible until some later working-tree change causes a watch cycle to
enqueue git ingest.

### Note history

`put()` compares the new content hash only against the current head.
If the new content differs from the current head, the current head is
archived and the new content becomes current.

This means `A -> B -> A` already preserves all states:

1. head `A`
2. write `B` -> archive `A`, head becomes `B`
3. write `A` -> archive `B`, head becomes `A`

The later `B` is not deleted. What is missing is recognition that the
new head `A` matches an already-archived version.

## Design

### 1. Add explicit repo-state fingerprinting to directory watches

Extend `WatchEntry` with repo-state fields for directory watches:

- `git_repo_root`
- `git_head`

These fields are informational as well as operational. They should be
stored in `.watches` alongside the existing working-tree fingerprint.

Add a small helper in `keep/watches.py`:

- resolve whether the watched directory is inside a git repo
- resolve the repo root
- resolve `HEAD` to a concrete commit SHA
- return empty values when the directory is not in a repo or git is not
  available

On watch creation:

- compute the current working-tree walk hash as today
- compute the git repo root and resolved `HEAD`
- persist both

On each directory poll:

- recompute the working-tree walk hash
- recompute the resolved `HEAD`
- treat the directory as changed if either value changed

This is intentionally narrower than "watch `.git`". We want one stable
signal for semantic repo movement, not every internal implementation
detail inside `.git`.

### 2. Keep git ingest queueing coalesced

When a watched repo changes, enqueue git ingest exactly as today, using
the repo-root supersede key.

This preserves the existing low-noise properties:

- duplicate requested work is superseded in the queue
- already-claimed older work is skipped if a newer item exists
- git ingest remains incremental via `git_watermark`

No new queue model is required.

### 3. Treat archived-hash match as a restore signal

When `put()` sees content different from the current head, look up the
note's archived versions by content hash using
`find_version_by_content_hash()`.

If there is no match:

- proceed exactly as today

If there is a match:

- still archive the current head
- still write the restored content as the new head
- record restore provenance on the head

Suggested provenance tags:

- `_restored_hash`
- `_restored_from_version`

This keeps history append-only and linear. It does not try to reinterpret
older archived versions as a branch or "future".

### 4. Do not introduce branch semantics into note history

The current version APIs are linear and offset-based. That is a useful
constraint. A restore-to-archived-hash event should remain representable
as "new current head that matches a prior archived state", not as a
branch-aware history rewrite.

This avoids several larger problems:

- ambiguous offset navigation
- special handling for "future" versions
- changes to `revert()` behavior
- more invasive UI and API changes

The note's version history remains a timeline of states that happened.
Some states may share the same content hash.

### 5. Reuse prior derived artifacts selectively on restore

The value of archived-hash recognition is not just provenance. It should
also prevent repeated processing cost when users move between already-known
states.

When a note's new head content matches an archived version of the same
note, keep should restore the previously-known derived outputs for that
content and skip reprocessing for the main retrieval path:

- summary
- auto-tagging outputs
- embeddings

This is an intentional tradeoff. Summary and auto-tagging are
not purely content-derived in keep; prompt selection, gathered context,
and tags can affect their output. Restoring prior outputs can therefore
produce a small semantic staleness gap when the environment around the
content has changed but the content itself has not.

That tradeoff is acceptable here. In practice, most semantically
important changes involve content change, while repeated checkout/branch
movement over known content can create large avoidable processing cost.

Embeddings remain the clearest case for reuse, but they should not be
treated as special. The restore path should prefer "known content,
known outputs" over recomputation where that directly affects normal
retrieval.

Constraints:

- reuse only within the same note ID
- require archived content-hash match
- preserve provider-identity safeguards for embeddings
- keep restore provenance on the head so the decision is visible
- do not version-track analysis parts

Not reused:

- outputs for a different note that happens to share the same content
- outputs whose source archived version cannot be resolved cleanly
- per-version analysis parts and their embeddings

Analysis parts are intentionally out of scope for restore. They are a
secondary discovery mechanism, expensive to recompute, and expensive to
version-track. On restore, any currently-attached parts stay in place
until `analyze` runs again, even if they describe a later state.

If later evidence shows the semantic gap is materially harmful, keep can
add an explicit "refresh derived outputs" path. That should be a later
opt-in correction, not the default restore behavior.

## Example

Assume a file note moves through these states:

- `A` on `main`
- `B` on feature branch
- checkout back to `A`
- checkout forward to `B`

Desired behavior:

1. The repo watch notices `HEAD` moved on each checkout.
2. Git ingest runs on each repo event, but only indexes genuinely new
   commits.
3. File notes whose content returns to `A` or `B` recognize the archived
   content-hash match.
4. The current head changes, but known derived work is restored without
   reprocessing.
5. Version history remains linear:
   archived states are preserved, not reclassified as branches.

## Testing Plan

### Watch tests

- commit-only event changes repo fingerprint without working-tree change
- empty commit is detected by a watched repo
- checkout to older commit changes repo fingerprint
- checkout back to newer branch changes repo fingerprint again
- non-git directory watches remain unchanged

### Git ingest tests

- repo event with no new commits is a no-op, not an error
- previously indexed commits are not duplicated after branch switching

### Note/version tests

- `A -> B -> A` records restore provenance on the final head
- `A -> B -> A -> B` also recognizes the later archived hash match
- archived versions remain present after restore-shaped writes
- embedding reuse occurs on archived-hash match
- summary/analyze/tag outputs are restored from the archived match and
  background reprocessing is skipped

## Documentation Impact

Update:

- `docs/KEEP-PUT.md`
- `docs/VERSIONING.md`

Document the following behavior:

- watched git repos notice `HEAD` movement, not just working-tree file
  changes
- git tags are still not a watch trigger
- restoring old content does not branch version history
- keep restores prior derived work when the same note content reappears,
  instead of reprocessing it by default

## Risks

### Watermark behavior on non-fast-forward moves

`git_watermark..HEAD` works naturally for forward progress, but backward
or divergent moves should be tested explicitly. This design accepts
"detected repo event with no new commits to ingest" as a valid outcome.

### Semantic staleness on restore

Restoring prior summary/analyze/tag outputs can be slightly stale if the
surrounding semantic context changed while the content did not.

This design accepts that risk. The expected cost of stale derived output
is smaller than the repeated processing cost of branch switching and
checkout restores over already-known content.

### Hidden semantic coupling

If restore recognition starts changing version navigation or revert
behavior, the scope has drifted. This note deliberately keeps restore
recognition separate from history semantics.

## Acceptance Criteria

- a watched repo notices commit-only `HEAD` movement
- git ingest is triggered by repo movement without watching `.git`
- duplicate git work remains coalesced
- a note can recognize when new content matches an archived version
- restored-prior-content writes preserve linear version history
- prior derived outputs are restored without recomputation when archived
  content reappears
- docs explain the new behavior clearly
