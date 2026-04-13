# Edge Tags

Edge tags turn ordinary tags into navigable relationships.  Usually if a note has tag `speaker: Kate`, this is just a label.  But when `speaker` is an *edge tag*, this becomes a reference (a graph edge) between the note and another note "Kate".  Moreover, that note "Kate" has edges pointing back to all the notes where it was tagged.

This behavior is defined by the *tagdoc*: the note that defines the tag, which has id like `.tag/*`.  When a `.tag/KEY` document declares `_inverse: VERB`, any document tagged with `KEY=target` creates a link to the target — and the target gets an automatic inverse listing under the `VERB` key in the unified `tags:` block.

Edge definitions are user-editable on purpose. They let you decide which relationships matter enough to become first-class navigation for you and your agent.

- Add edges to make important entities easier to traverse.
- Rename inverses to fit your domain language.
- Remove edges when they create noise instead of signal.

## How it works

The bundled `.tag/speaker` tagdoc has `_inverse: said`. This means:

```bash
# Tag a conversation part with a speaker
keep put "I think we should refactor the auth module" -t speaker=Deborah

# Deborah now has an inverse listing
keep get Deborah
```

Output:

```yaml
id: Deborah
tags:
  said:
    - conv1@P{5} [2025-03-15] "I think we should refactor the auth module"
    - conv2@P{3} [2025-03-18] "The API needs rate limiting..."
```

The `said` entries under `tags:` are computed from the edges table — they're not stored as tags on `Deborah`. Each entry links back to the source document, rendered as `id [date] "summary"`.

## Stub creation

If the target doesn't exist, keep creates a stub note for it automatically. In the example above, `speaker=Deborah` creates a stub `Deborah` note if one doesn't exist yet. You can add content to it later:

```bash
keep put "Deborah is the tech lead on project X" --id Deborah
```

The inverse edges survive — the `said` entries under `tags:` still show everything Deborah said.

## Creating edge tags

Any tag can become an edge tag by adding `_inverse` to its tagdoc. Edge tagdocs are system documents, so `_inverse` is set via the tagdoc's frontmatter (like `_constrained`), not through `keep put -t`.

To create a custom edge tag, write a tagdoc with `_inverse` in its tags:

```bash
keep put "$(cat <<'EOF'
---
tags:
  _inverse: contents
---
# Tag: `contains`

Items that contain other items. The inverse `contents` shows
what container an item belongs to.
EOF
)" --id .tag/contains
```

Now `contains=item-B` on document A creates an edge, and `get item-B` shows `contents: A [date] "summary"` in its `tags:` block.

### Symmetric tagdocs

When `.tag/contains` declares `_inverse: contents`, keep automatically creates `.tag/contents` with `_inverse: contains` (if it doesn't already exist). This makes the relationship navigable in both directions — tagging with either key creates edges that the other key can resolve. If `.tag/contents` already exists with a different `_inverse`, that's a conflict error.

### Backfill

When you add `_inverse` to an existing tagdoc, keep automatically backfills edges for all documents already tagged with that key. This runs in the background — edges may take a moment to appear.

## Bundled edge tags

### General

| Tag | `_inverse` | Example | Meaning |
|-----|-----------|---------|---------|
| `speaker` | `said` | `speaker: Deborah` on a turn | `get Deborah` → `said:` entries |
| `user_id` | `user_id_of` | `user_id: contact:telegram:42` on a Hermes note | `get contact:telegram:42` → `user_id_of:` entries |
| `informs` | `informed_by` | `informs: auth-decision` on a URL | `get auth-decision` → `informed_by:` entries |
| `references` | `referenced_by` | `references: other-note` via link extraction | `get other-note` → `referenced_by:` entries |
| `cites` | `cited_by` | `cites: [[arxiv:2403.04782\|Title]]` on a paper | `get arxiv:2403.04782` → `cited_by:` entries |
| `duplicates` | `duplicates` | `duplicates: notes-v1` on a duplicate | Symmetric: both sides show `duplicates:` |
| `author` | `authored` | `author: alice@example.com` on a git commit | `get alice@example.com` → `authored:` entries |

### Email

| Tag | `_inverse` | Example | Meaning |
|-----|-----------|---------|---------|
| `from` | `sender_of` | `from: alice@example.com` on an email | `get alice@example.com` → `sender_of:` entries |
| `to` | `recipient_of` | `to: bob@example.com` on an email | `get bob@example.com` → `recipient_of:` entries |
| `cc` | `cc_recipient_of` | `cc: carol@example.com` on an email | `get carol@example.com` → `cc_recipient_of:` entries |
| `bcc` | `bcc_recipient_of` | `bcc: dave@example.com` on an email | `get dave@example.com` → `bcc_recipient_of:` entries |
| `in-reply-to` | `has_reply` | `in-reply-to: <msg-id>` on a reply | `get <parent>` → `has_reply:` entries |
| `attachment` | `has_attachment` | `attachment: email-id` on an attachment | `get email-id` → `has_attachment:` entries |

### Git

| Tag | `_inverse` | Example | Meaning |
|-----|-----------|---------|---------|
| `git_commit` | `git_file` | `git_commit: git://repo#abc` on a file | `get git://repo#abc` → `git_file:` entries |

## Rules

- **Case-sensitive values**: `speaker: Deborah` and `speaker: deborah` link to different targets. Be consistent.
- **Multi-valued**: A document can have multiple values per edge tag (e.g., `speaker: [alice, bob]`). Each value creates a separate edge. Multiple documents can point at the same target.
- **Singular edge tags**: An edge tag with `_singular: true` on its tagdoc replaces the old value (and its edge) when a new value is set. For example, an `assignee` edge tag that is singular would reassign the edge rather than accumulating multiple assignees.
- **System doc targets skipped**: Tag values starting with `.` (like `.meta/todo`) don't create edges.
- **Removal**: Setting a tag to empty (`-t speaker=`) deletes that edge without affecting other edges on the document.

## When to promote a tag to an edge tag

Edge tags point at real-world entities. The decisive test is not cardinality — it's **referential integrity**: do the tag values already point at things that exist (or that you intend to exist) as first-class notes?

- `speaker: Deborah` → **edge**. Deborah is a person who exists as a note. You want to navigate from Deborah to everything she said.
- `from: alice@example.com` → **edge**. The email address is a stable identifier for a contact entity.
- `topic: rust` → **not an edge**. "rust" is a taxonomy label for filtering, not an entity you'd navigate to. There's no "rust" note that accumulates inbound references.
- `project: keep` → **could be an edge**, if `keep` is a first-class project note that collects all related work. If it's just a filtering label, leave it as a plain tag.

If you find yourself wanting to `keep get <tag-value>` and see everything that references it, you have a latent edge. Promote the tag by adding `_inverse` to its tagdoc. If you only ever use the tag for `keep find -t key=value` filtering, it's a taxonomy label and should stay a plain tag.

## Edge vs meta: choosing the right tool

Use **edge tags** for explicit graph relationships:

- "Who said this?"
- "What does this contain?"
- "Which notes point at this entity?"

Use **meta docs** for contextual reflection policy:

- "What open commitments should appear here?"
- "What learnings should be surfaced in this project?"
- "What context should appear only when prerequisite tags exist?"

Edge tags optimize navigability and relationship fidelity.
Meta docs optimize relevance and situational awareness.

## Finding edge sources

Outbound edges are normal tags, so `keep find` works:

```bash
keep find -t speaker=Deborah    # All docs where Deborah is the speaker
```

Inverse edges (the resolved `said:` entries in `tags:`) are only visible through `keep get` on the target.

## See Also

- [TAGGING.md](TAGGING.md) — Tag descriptions, constrained values, filtering
- [META-TAGS.md](META-TAGS.md) — Contextual queries (`.meta/*`)
- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Auto-managed system tags (`_created`, `_updated`, etc.)
