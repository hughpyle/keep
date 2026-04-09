# Markdown Sync Design

Date: 2026-04-08
Status: Draft

## Problem

The current markdown export is a snapshot CLI feature. It is not a sync model.

There are three distinct gaps:

- content role is implicit across multiple write and post-write paths
- frontmatter mixes true tags with note identity/export metadata
- export lives in the CLI, while continuous mirroring belongs in the daemon

This becomes visible immediately when considering Obsidian-style editing:

- exported wikilinks must resolve to exported vault paths, not raw keep ids
- the markdown body must round-trip as authoritative note content when sync is
  enabled
- frontmatter must separate user-editable tags from reserved sync metadata

## Goals

- Keep markdown export usable as a browsable Obsidian-style vault.
- Make sync semantics explicit instead of inferred from current write heuristics.
- Move markdown export ownership into the daemon.
- Allow more than one synced export root.
- Support continuous one-way export first.
- Add bidirectional import only after content and frontmatter policy are clear.

## Non-Goals

- Do not add a second persisted text field or a new storage mode.
- Do not support filtered sync in the first version.
- Do not make parts and archived versions writable in the first sync version.
- Do not make the CLI responsible for long-running mirror state.

## Design

## 1. Content Role Must Be Explicit

The core issue is not raw storage capacity. Keep already carries full write-time
text through parts of the processing pipeline. The problem is that the role of
that text is not clearly flagged in one place.

Today the system effectively has multiple text roles:

- authoritative note content
- derived summary text
- derived description/enrichment text

But these roles are decided indirectly across several paths:

- initial `put()` summary selection
- write-context capture for after-write processing
- summarize/describe mutations that later replace the stored note body

Sync requires an explicit content-role policy.

Required change:

- introduce one centralized content-role decision path used by note writes,
  URI ingest, markdown-sync import, and mutation application
- define that policy concretely in the companion doc
  [markdown-sync-content-role-policy.md](/Users/hugh/play/keep/later/design/markdown-sync-content-role-policy.md)

Required behavior for sync-managed markdown notes:

- the markdown body is authoritative note content
- background summarize/describe must not overwrite that body
- analyze, auto-tag, and link extraction may still read it

This is a semantic refactor, not a schema expansion.

## 2. Frontmatter Must Distinguish Tags From Reserved Metadata

For sync, the exported frontmatter needs two classes of keys:

- user-editable tags
- reserved sync/export metadata

`id:` was the wrong spelling for note identity in this model. It looks like a
normal top-level key and is too easy to confuse with an editable tag.

Use reserved underscore-prefixed metadata instead:

- `_id` = canonical keep note id

Reserved `_...` keys are not ordinary tags. They are interpreted by the
markdown-sync importer through a dedicated policy function.

That policy function should answer, for each frontmatter key:

- is it note identity metadata
- is it a writable user tag
- is it derived/read-only export metadata
- should it be ignored on import

There should be exactly one helper responsible for this classification.

Export, sync import, and any future markdown-edit path should all call the same
helper rather than carrying separate local rules about which keys are mutable,
reserved, exporter-owned, or note tags.

This is the frontmatter counterpart to the content-role unification: the goal is
to remove scattered one-off decisions and replace them with one explicit
decision point.

Examples of reserved read-only export metadata:

- `_id`
- `_content_hash`
- `_content_hash_full`
- `_created`
- `_version`
- `_version_offset`
- `_part_num`
- `_prev_part`
- `_next_part`
- `_prev_version`
- `_next_version`

This keeps note identity and navigation metadata out of the user tag namespace.

## 3. Exported Markdown Uses An Export Namespace, Not Keep IDs

Markdown export already needs a vault-local link namespace derived from final
on-disk paths so tools like Obsidian can resolve links.

That namespace should be treated as first-class.

For any exported note or sidecar:

- canonical keep identity remains the keep id
- exported markdown identity is the vault-relative path stem

Example:

- keep id: `https://example.com/docs/guide`
- exported ref: `https/example.com/docs/guide`
- exported file: `https/example.com/docs/guide.md`

The exported wikilink target must match the exported path stem exactly.

## 4. Daemon-Owned Mirror

Continuous markdown export belongs in the daemon, not the CLI.

Reasons:

- it is long-running state
- it depends on change notifications
- it must coordinate path mapping and atomic rewrites
- it will later share machinery with bidirectional sync

The CLI should stay a thin wrapper around daemon-owned mirroring state, but the
current checkpoint still keeps one-shot export and the initial `--sync` seed
pass local.

Current behavior:

- one-shot markdown export runs locally in the CLI against the current store
- `--sync` first validates the target root with the daemon
- the CLI then performs one local full seed export with a progress bar
- the daemon finally records the mirror registration and owns ongoing updates

This split is intentional for now because the initial seed can take a long time
and the local CLI path provides better foreground progress feedback.

The daemon remains responsible for:

- mirror registration state
- overlap validation
- ongoing outbound change handling
- future inbound watch/import work

## 5. Mirror Registrations

The daemon should support multiple markdown mirrors.

Each mirror registration is independent and has its own:

- root directory
- export options
- `.keep-sync/map.tsv`
- `.keep-sync/state.json`

This should be designed as a list from the start, not as a singleton.

However, v1 should not support filtered sync.

Rationale:

- multiple full mirrors are cheap and structurally clean
- filtered sync changes link semantics, inverse-edge visibility, and import
  authority in ways that deserve separate design work

So the initial model is:

- many mirrors
- each mirror is a full-store mirror
- no query-scoped, tag-scoped, or partial writable sync

### Mirror options

Initial mirror registration options should be limited to export-shape options:

- `include_system`
- `include_parts`
- `include_versions`
- `interval`
- `enabled`

No query-scoped or tag-scoped `filter` option in v1.

`include_system` is still allowed. It is a structural shape option, not an
arbitrary content filter.

### Exclusivity

A markdown mirror root must be exclusive with ordinary file-ingest watching.

The system should actively prevent these overlapping configurations:

- registering markdown sync on a directory that is already watched via
  `keep put PATH --watch`
- adding a file or directory watch whose path is inside a markdown mirror root
- registering markdown sync on a path that is inside an existing watched
  directory

For the same reason, keep should reject direct file-based `put` operations from
inside a synced mirror root.

Examples that should be rejected:

- `keep put notes/foo.md`
  when `notes/` is a synced markdown mirror
- `keep put notes/ --watch`
  when `notes/` is a synced markdown mirror
- `keep data export notes/ --format md --sync`
  when `notes/` is already covered by a file or directory watch

Rationale:

- markdown sync is a note-identity-aware mirror
- `put --watch` is a source-ingest mechanism
- running both on the same files creates ambiguous ownership and invalid
  reconciliation behavior

The daemon should enforce this path exclusivity when registering mirrors and
watches. The CLI should surface a clear error.

## 6. Minimal On-Disk Mirror Metadata

The mirror should use two small files under the export root:

- `.keep-sync/map.tsv`
- `.keep-sync/state.json`

### `.keep-sync/map.tsv`

This is the namespace bridge used by markdown sync.

Format:

- UTF-8 text
- header row:
  `export_ref<TAB>keep_id`
- one line per exported note or sidecar
- each `export_ref` uses the same vault-relative path-stem encoding as the
  export writer's path planner

Examples:

```text
https/example.com/docs/guide	https://example.com/docs/guide
_email/example.com/alice@example.com	alice@example.com
project/doc/@P{1}	project/doc@P{1}
```

Notes:

- the left side is the vault-relative path stem, not a basename
- the mapping is plaintext, diffable, and easy to inspect
- the mapping is deterministic and fully regenerable from keep state plus
  export options

### `.keep-sync/state.json`

This file should stay minimal.

It does not need to duplicate per-note content hashes or timestamps, because
those already exist in keep and in the exported files.

It should hold only ephemeral mirror state such as:

- sync format/version
- active export options that affect mapping/layout
- minimal runtime bookkeeping for loop suppression or watcher state

The mapping file is semantic. The JSON state file is operational.

`.keep-sync/` is keep's sidecar directory. It is distinct from Obsidian's
`.obsidian/`. Both may coexist at the vault root and must not interfere with
each other.

## 7. CLI Surface

The CLI should expose sync as a daemon registration, not as a foreground loop.

Current shape:

```bash
keep data export PATH --format md
keep data export PATH --sync
keep data export PATH --sync --stop
keep data export --list
```

Semantics:

- `keep data export PATH --format md`
  - one-shot markdown export
- `keep data export PATH --sync`
  - ensure the directory is a registered markdown mirror
  - validate the root with the daemon before writing files
  - perform an immediate local seed export pass with a progress bar
  - register the mirror with the daemon as already seeded
  - leave the daemon responsible for keeping it updated
- `keep data export PATH --sync --stop`
  - remove that mirror registration
  - do not delete the exported files
- `keep data export --list`
  - list registered markdown mirror roots and current status

The CLI should reject invalid combinations:

- `--stop` requires `--sync`
- `--list` cannot be combined with `--sync` or `--stop`
- `--list` does not take an output path

`--sync` and `--stop` currently imply markdown export mode, so `--format md`
does not need to be spelled out in those cases.

The daemon still stores a per-mirror interval and uses it as the debounce
window, but that option is currently hidden from the CLI until the operational
surface is clearer.

The CLI should also reject `put` of files rooted inside a synced mirror
directory, with a message directing the user to edit the synced markdown file
directly or disable sync first.

## 8. Outbound Sync Trigger

Continuous export should be driven by daemon change notifications, not by a
blind periodic full export.

The interval is a debounce and coalescing timer, not the primary source of
truth. The daemon should enqueue mirror work when keep mutations affect the
exported surface.

This should be centralized at the persistence boundary.

The design should not scatter `mark_mirror_dirty(...)` calls across many
application call sites. That would recreate the same kind of semantic drift
that this sync design is trying to remove.

### Sync outbox

The preferred mechanism is a dedicated persisted sync outbox owned by the
datastore layer.

That outbox should record semantic mirror-relevant mutations such as:

- document insert/update/delete
- document body update
- document tag update
- edge insert/update/delete
- part insert/update/delete
- version insert/delete

The daemon tick drains that outbox and updates affected mirrors.

This keeps the trigger:

- centralized
- durable across daemon restarts
- independent of whichever high-level API path happened to cause the mutation

### Why not ad hoc calls

Body changes currently arrive through more than one path:

- direct note writes
- background summarize/describe/OCR mutations

If sync triggering were added by hand at each of those sites, call-site
proliferation would become a correctness risk.

The sync trigger should therefore be generated from persisted mutations, not
from individual feature code paths.

### Relation to existing outboxes

The existing `planner_outbox` is precedent for this shape, but it is not
sufficient as-is.

In particular, current planner outbox coverage does not fully model all
mirror-relevant changes such as summary-only body updates from later mutation
application.

So sync should either:

- get its own `sync_outbox`, or
- extend the outbox/trigger layer until it fully covers mirror-relevant
  mutations

The important requirement is centralization at the datastore boundary, not
reuse for its own sake.

Outbound mirror updates should trigger on:

- note create/update/delete
- note tag changes
- part create/update/delete
- archived version create/delete
- edge changes that affect inverse-edge frontmatter

The daemon should translate those low-level mutations into affected exported
objects through an explicit dependency model. There are four distinct outbound
dependency classes.

This dependency model should not live inside markdown export or mirror code.
Reverse-dependency traversal is a general capability that other features will
also need.

So the implementation should provide one shared note-dependency service in the
core. Markdown export should consume that service rather than calling raw edge
queries directly.

That service should own both:

- the semantic API for dependency traversal
- the execution strategy used to answer it

The semantic API should cover at least:

- notes directly affected by a mutation
- current-note targets reached by `edges` from a source note
- current-note sources that point at a target note via `edges`
- archived-version targets reached by `version_edges` from a source note
- archived-version sources that point at a target note via `version_edges`
- structural sidecars attached to a note id (parts, versions)

The execution strategy should be hidden behind the service boundary:

- start with query-backed resolution over the existing indexed `edges`,
  `version_edges`, `document_parts`, and `document_versions` tables
- allow the service to switch later to a materialized dependency tracker if
  indexed queries are no longer good enough

Callers should not need to know which strategy was used.

### Direct surface dependencies

These are the exported files directly attached to one keep note id:

- the current note file
- its part sidecars, when parts are exported
- its version sidecars, when archived versions are exported

Examples:

- changing a note body or writable tags rewrites that note's exported file
- creating `@P{3}` rewrites the parent file and the affected part sidecars to
  keep `_next_part` / `_prev_part` links correct
- archiving a new version rewrites the parent file and the affected version
  sidecars to keep version navigation correct

### Inverse-edge target dependencies

Edges are rendered in both directions in markdown export, but inverse edges are
not stored on the target note itself. They are reconstructed by querying the
edge tables.

That means an edge mutation has two outbound surfaces:

- the source note, because its forward edge tags/frontmatter may have changed
- the target note, because its inverse-edge frontmatter may have changed

This applies to both current-note edges and archived-version edges.

Examples:

- adding a `references` edge rewrites the source note and the target note
- deleting a `speaker` edge rewrites the source note and the target note
- adding or deleting a `version_edges` row rewrites the affected target note's
  archived-version inverse-edge frontmatter

### Inverse-edge source-display dependencies

Inverse-edge rendering includes a formatted source reference, not just a bare
id. So some mutations on note `A` require rewriting other notes that point to
`A`, even when no edge rows changed.

At minimum, display-relevant source changes include:

- note summary/body changes when the display name is derived from summary text
- tag changes that affect `note_display_name(...)`

For these mutations, the daemon must rewrite:

- the changed source note itself
- any current target notes that have inverse edges from that source
- any archived-version target notes that have inverse version edges from that
  source

This fanout is required for correctness. Incremental export cannot be defined
only in terms of "the note that changed".

### Structural path-planning dependencies

Some mutations can change the export namespace itself rather than only the
content of already-known files.

These require a broader replan because `map.tsv`, disambiguated filenames, or
sidecar placement may change:

- first mirror creation
- mirror option changes affecting layout
- note creation or deletion
- note id change / move
- any change that can introduce or remove a path collision and therefore change
  a disambiguated filename

These are the cases where full map rebuild is required rather than a bounded
incremental rewrite.

### Non-dependencies

Not every stored mutation should wake the mirror.

In particular, access-time churn (`touch`, `_accessed` only) should not trigger
outbound sync. Continuous markdown export is a write mirror, not a live replica
of read-side metadata changes.

Examples:

- changing a note body or tags rewrites that note's exported file
- adding a `references` edge rewrites the source note and the target note
  because the target's inverse edges changed
- creating `@P{3}` rewrites the parent file and the affected part sidecars to
  keep `_next_part` / `_prev_part` links correct
- archiving a new version rewrites the parent file and the affected version
  sidecars to keep version navigation correct

The daemon should coalesce repeated changes within the configured interval and
then perform one bounded mirror update pass per mirror.

Ordinary content and tag updates should usually reuse the existing map and
rewrite only the notes reached through the dependency graph above.

### Current implementation notes

The current codebase now has the correct trigger boundary and a first
incremental dependency response.

Specifically:

- `sync_outbox` is currently collection-agnostic at drain time. This is
  acceptable with today's effectively single-collection runtime, but true
  multi-collection support will need mirror registrations and outbox handling
  to become collection-aware.
- edge and version-edge trigger payloads are currently source-oriented. The
  implementation resolves target-side impact through the shared dependency
  service rather than relying on the payload alone.
- large bulk mutation streams can require multiple outbox-drain ticks before
  the mirror reaches its debounce/export phase. This is acceptable for the
  current checkpoint, but throughput behavior should be revisited once
  incremental export narrows the affected-set rewrite cost.
- the debounce window now stores a pending mirror-update plan on the mirror
  entry itself. This is required so a due poll can still perform a bounded
  rewrite after the original outbox rows have been drained.
- structural mutations still force a full mirror replan. Bounded incremental
  rewrites currently cover ordinary note updates, part/version changes, and
  edge-driven inverse-edge fanout.

## 9. Continuous Export First

The first implementation milestone should be daemon-owned continuous export,
not bidirectional sync.

This stage includes:

- moving markdown render/planning code out of CLI-only helpers and into shared
  service code used by both CLI export and daemon mirrors
- registering a markdown mirror rooted at a directory
- writing `.keep-sync/map.tsv`
- defining and implementing the outbound dependency graph above
- updating only affected exported files on keep changes, except when a full
  path replan is required

This milestone is now implemented as the current outbound checkpoint:

- dependency traversal is provided by a shared core service rather than export-
  specific edge queries
- mirror polling performs bounded note-bundle rewrites for non-structural
  mutations
- note create/delete and other namespace-shaping changes still use full replan
- the initial `--sync` seed export still runs locally in the CLI, but ongoing
  mirror ownership and change handling live in the daemon

This should use existing change notifications. The hard part is not detection;
it is stable namespace planning and file mapping.

At this stage the flow is one-way:

- keep -> markdown vault

No folder watch/import yet.

## 10. Bidirectional Import Comes Last

Folder watch and import should land only after:

- content-role policy is explicit
- frontmatter-key policy is explicit
- daemon-owned export/mirror machinery is in place

The import path must be dedicated markdown-sync logic, not generic markdown
frontmatter extraction.

It must:

- read `_id` as note identity
- treat reserved `_...` keys according to the centralized metadata policy
- import writable tags and authoritative body content
- ignore read-only export navigation keys
- perform loop suppression and version-aware reconciliation

In the first sync version, current notes should be the only writable targets.
Parts and archived versions should remain exported but read-only.

### Markdown-only note sync

For now, synced vaults should be defined as markdown-note mirrors, not generic
filesystem-ingest trees.

That means:

- markdown files participate in note sync
- non-markdown files inside the vault are treated as local assets
- non-markdown files are not imported as keep notes in v1

This is an intentional boundary.

It avoids mixing two incompatible identity models:

- vault-relative note identity for synced markdown
- filesystem-location identity for `put file://...`

### New markdown files

New markdown files created in a synced vault should import cleanly.

Rules:

- if frontmatter contains `_id`, use that keep id
- otherwise derive the keep id from the vault-relative path stem

Example:

- file path: `projects/auth/ideas.md`
- imported keep id: `projects/auth/ideas`

After the next export pass:

- `_id` is written into frontmatter
- `.keep-sync/map.tsv` gains the corresponding `export_ref<TAB>keep_id` entry

This keeps exported and imported markdown in the same namespace.

### Non-markdown files

Non-markdown files inside a synced vault should remain untouched by note sync.

Examples:

- images
- PDFs
- other local attachments

They may still be referenced from synced markdown by ordinary markdown links,
but they are not automatically imported as notes.

This is a deliberate simplification for v1. The question of whether vault
assets should later become first-class synced notes is deferred.

### Body wikilinks

Markdown body content remains authoritative user text.

Body wikilinks such as `[[other-note]]` or `[[other-note|Label]]` should be:

- preserved verbatim in the body
- processed by normal link extraction so `references` edges and inverse edges
  still work

This keeps body text faithful to what the user typed while still meeting normal
 backlink expectations.

### Reserved-key edits

If a user edits read-only reserved keys such as `_prev_part` or
`_next_version`, the importer should:

- ignore the change
- optionally log once at info/warning level
- restore the canonical value on the next export pass

The same rule applies to other derived export metadata.

### Read-only sidecars

Parts and archived versions are exported but read-only in v1.

Operationally:

- importer detects edits to sidecar files
- importer ignores those edits
- next export pass rewrites the canonical sidecar content
- daemon logs a short message explaining that sidecars are read-only and the
  parent note is authoritative

This makes the behavior explicit instead of leaving users to infer it from
silent overwrites.

### Deletions

Vault deletion is not authoritative in v1.

If a synced markdown note file is deleted locally:

- keep does not delete the note
- the note is re-exported on the next mirror pass

Deletion through the vault is out of scope for v1, just like filtered sync.

### File moves

File location inside the vault is not authoritative in v1.

The keep id and export path rules decide the canonical file location.

If a user moves an exported markdown file within the vault:

- the note identity remains `_id`
- the moved file may be read as an edit to that same note
- the next export pass restores the file to its canonical export location

So the vault is not a user-controlled filing cabinet in v1. The keep id drives
layout.

## 11. Reconciliation Semantics

V1 should not use blocking conflicts as the normal sync model.

Instead, bidirectional sync should reconcile by versioning:

- the latest side wins for the current head
- the older distinct state is preserved as a non-head version

This matches keep's existing versioned note model and avoids turning sync lag
or long watch intervals into user-visible conflict errors.

### Current-head rule

For a synced current note:

- if the disk state is newer than the keep head, import it as the new head
- if the disk state is older than the keep head, preserve it as an archived
  version and keep the current keep head unchanged
- if the states are identical, do nothing

The same principle applies in the other direction:

- a newer keep head rewrites the exported file
- an older exported file snapshot never replaces a newer keep head

### Ordering source

This design assumes the daemon and sync folder live on the same machine.

Use:

- keep note update time for the keep side
- filesystem modification time for the disk side

This is intentionally simple. It tolerates:

- long polling intervals
- debounce/coalescing delay
- trigger lag between a write and the next mirror pass

because reconciliation compares the observed disk snapshot against the current
keep head, not against the exact moment the change was first noticed.

Hosted backends do not support markdown sync in v1. This design is for a local
daemon with direct access to the vault path. One-shot export remains possible
through the existing CLI path.

### Consequences

This means sync does not try to surface an explicit conflict artifact for
ordinary concurrent edits.

Instead:

- newer state becomes current
- older distinct state is preserved in version history

That is a better fit for keep than producing separate conflict files.

### Dedup

Version creation should still be deduplicated.

If the imported disk snapshot is byte-identical to the current keep head, or
matches an existing historical version already present, the importer should not
create redundant versions.

## 12. Invariants

The core integration invariant for this work should be:

- export -> import -> export is byte-identical for any note not concurrently
  mutated

That should be tested for:

- current notes
- notes with edge-tag frontmatter
- notes with body wikilinks
- notes with parts/versions exported as sidecars
- path-disambiguated notes
- synced vaults containing `.keep-sync/` and `.obsidian/`

## Sequencing

Implementation order:

1. Write and review the companion content-role policy doc:
   [markdown-sync-content-role-policy.md](/Users/hugh/play/keep/later/design/markdown-sync-content-role-policy.md)
2. Fix frontmatter export shape first.
   - switch exported note identity to `_id`
   - make reserved export metadata explicit
   - preserve current one-shot export behavior otherwise
3. Centralize content-role policy.
   - do this in a way that preserves existing end-to-end invariants for normal
     note writes
   - the goal is to make sync semantics explicit without destabilizing current
     behavior
4. Move shared markdown rendering/planning into non-CLI service code.
   - one-shot CLI export may still remain local for UX reasons
5. Add mirror registration and continuous one-way export with `.keep-sync/map.tsv`
   and minimal `.keep-sync/state.json`.
6. Add incremental outbound export using the shared dependency model.
7. Add folder watch/import using the new policies and mirror state, with
   latest-write-wins plus archived older versions.

Steps 1 through 6 are now complete enough for the current outbound-only
checkpoint. The main remaining work is step 7: inbound watch/import and
reconciliation.

## Consequences

Benefits:

- exported markdown becomes a stable vault model instead of an incidental CLI
  rendering
- sync semantics are explicit
- mapping is transparent and repairable by humans
- daemon and CLI responsibilities stay clean

Tradeoff:

- markdown export is no longer just a dumb file dump; it becomes a daemon-owned
  mirror with a small explicit namespace contract

That tradeoff is worth making because bidirectional sync requires an explicit
contract anyway.
