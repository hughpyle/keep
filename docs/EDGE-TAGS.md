# Edge Tags

Edge tags turn ordinary tags into navigable relationships. When a `.tag/KEY` document declares `_inverse: VERB`, any document tagged with `KEY=target` creates a link to the target — and the target gets an automatic inverse listing under `tags/VERB:`.

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
tags: {_source: auto-vivify}
tags/said:
  - conv1@P{5}   [2025-03-15]  I think we should refactor the auth module
  - conv2@P{3}   [2025-03-18]  The API needs rate limiting...
```

The `tags/said:` section is computed from the edges table — it's not stored as a tag on `Deborah`. Each entry links back to the source document, shown with its date and summary.

## Auto-vivification

If the target doesn't exist, it's created as an empty document automatically. In the example above, `speaker=Deborah` creates a `Deborah` document if one doesn't exist yet. You can add content to it later:

```bash
keep put "Deborah is the tech lead on project X" --id Deborah
```

The inverse edges survive — `tags/said:` still shows everything Deborah said.

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

Now `contains=item-B` on document A creates an edge, and `get item-B` shows `tags/contents: A`.

### Backfill

When you add `_inverse` to an existing tagdoc, keep automatically backfills edges for all documents already tagged with that key. This runs in the background — edges may take a moment to appear.

## Bundled edge tags

| Tag | `_inverse` | Example | Meaning |
|-----|-----------|---------|---------|
| `speaker` | `said` | `speaker: Deborah` on a turn | `get Deborah` → `tags/said: [turns...]` |

## Rules

- **Case-sensitive values**: `speaker: Deborah` and `speaker: deborah` link to different targets. Be consistent.
- **Single-valued**: Each document can have one value per edge tag (e.g., one `speaker`). Multiple documents can point at the same target.
- **System doc targets skipped**: Tag values starting with `.` (like `.meta/todo`) don't create edges.
- **Removal**: Setting a tag to empty (`-t speaker=`) deletes that edge without affecting other edges on the document.

## Finding edge sources

Outbound edges are normal tags, so `keep find` works:

```bash
keep find -t speaker=Deborah    # All docs where Deborah is the speaker
```

Inverse edges (`tags/said:`) are only visible through `keep get` on the target.

## See Also

- [META-TAGS.md](META-TAGS.md) — Tag descriptions, contextual queries, prompt overrides
- [TAGGING.md](TAGGING.md) — Tag basics: setting, filtering, isolation
- [SYSTEM-TAGS.md](SYSTEM-TAGS.md) — Auto-managed system tags (`_created`, `_updated`, etc.)
