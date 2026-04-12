# System Tags Reference

System tags are automatically managed metadata prefixed with underscore (`_`).
Users cannot set or modify them directly — they are filtered out of user-supplied
tag maps by `filter_non_system_tags()` in `keep/types.py`.

There are three rough groups:

- **Lifecycle tags** — `_created`, `_updated`, `_accessed` (and their `_date`
  variants) track when notes were written and read.
- **Provenance tags** — `_source`, `_content_type`, `_source_uri` describe
  where the content came from.
- **Pipeline / projection tags** — `_summarized_hash`, `_analyzed_version`,
  `_content_length`, `_ocr_pages`, `_supernode_reviewed`, plus the
  `_focus_*`, `_anchor_*`, and `_lane` family used by deep-find projection.
  Some of these (the date variants and the focus/anchor/lane family) are
  also listed in `INTERNAL_TAGS` in `keep/types.py` so they get hidden from
  default display output even though they're queryable.

## Timestamp format

All timestamps in keep are UTC, stored in canonical form **without microseconds
and without timezone suffix**:

```
YYYY-MM-DDTHH:MM:SS
```

For example: `"2026-04-08T13:09:51"`. The single source of truth for this
format is `utc_now()` in `keep/types.py`.

---

## Lifecycle tags

### `_created`

ISO timestamp of when the item was first indexed. Set on first insert,
preserved on updates. Both `DocumentStore` and `ChromaStore` maintain it.

```
"_created": "2026-04-08T13:09:51"
```

Read via `item.created` (datetime) or `item.tags["_created"]`.

### `_updated`

ISO timestamp of the last modification. Updated on every write — content,
summary, or tags.

```
"_updated": "2026-04-08T13:11:42"
```

Read via `item.updated` or `item.tags["_updated"]`.

### `_updated_date`

Date portion of `_updated`, format `YYYY-MM-DD`. Used by `--since`/`--until`
filters in CLI commands. Set automatically alongside `_updated`.

```
"_updated_date": "2026-04-08"
```

### `_accessed`

ISO timestamp of when the item was last retrieved. Updated whenever the item
is read via `get()` or `find()`. Distinct from `_updated` — reading touches
`_accessed` but not `_updated`.

```
"_accessed": "2026-04-08T13:09:55"
```

Read via `item.accessed` or `item.tags["_accessed"]`.

### `_accessed_date`

Date portion of `_accessed`. Same format as `_updated_date`.

---

## Provenance tags

### `_source`

How the content was obtained.

| Value | Meaning |
|-------|---------|
| `inline` | Inline content (CLI: `keep put "text"`, API: `kp.put("text")`) |
| `uri` | Content fetched from a URI (CLI: `keep put <uri>`, API: `kp.put(uri=...)`) |
| `langchain` | Created via `KeepStore` (LangChain/LangGraph integration) |
| `auto-vivify` | Stub note created from an edge target (e.g. `speaker=Deborah`) |

Query with `kp.list_items(tags={"_source": "inline"})` to find inline notes,
or `tags={"_source": "uri"}` to find indexed documents.

### `_content_type`

MIME type of the document content. Set by `Keeper.put()` for URI-based
documents (the document provider returns it) and for inline content
(defaults to `text/markdown`).

```
"_content_type": "text/markdown"
```

Common values: `text/markdown`, `text/html`, `text/plain`, `application/pdf`,
`audio/mpeg`, `image/jpeg`, `image/png`.

### `_source_uri`

The original URI when a URI-backed document is stored under a custom ID
(`doc_id != uri`). For example, indexing `https://example.com/doc` with
`--id my-doc` records `_source_uri="https://example.com/doc"` so the
original fetch URL is preserved even though the note is keyed by a custom
identifier. Set in `Keeper.put()`.

---

## Assessment tags

Set by the assessment flow (`.state/assess`) when a URL reputation check runs.

| Tag | Set by | Purpose |
|-----|--------|---------|
| `assessment_virustotal` | `assess_virustotal` action | Verdict: `ok`, `suspicious`, or `malicious` |
| `assessment_virustotal_checked_at` | `assess_virustotal` action | UTC ISO timestamp of the check |

These are only added when `VIRUSTOTAL_API_KEY` (or `VT_API_KEY`) is set and the target is an HTTP URL. Notes tagged `assessment_virustotal: malicious` have their content replaced with an explanatory message and do not trigger background processing.

Query assessed notes:
```python
kp.list_items(tags={"assessment_virustotal": "malicious"})
```

---

## Pipeline state tags

These tags exist on items so pipeline actions can skip work that's already
current. They are real stored tags (not hidden by `INTERNAL_TAGS`), and you
can query them directly.

| Tag | Set by | Purpose |
|-----|--------|---------|
| `_summarized_hash` | summarize action | Content hash that was summarized — re-summarize when it changes |
| `_analyzed_version` | analyze action | Version number that was analyzed — re-analyze when it changes |
| `_content_length` | put | Length of the regularized content (for summary heuristics) |
| `_ocr_pages` | document provider | List of page indices flagged for background OCR |
| `_supernode_reviewed` | review-supernodes flow | Timestamp marking a supernode factsheet that has been through the review pipeline |

The `analyze` action, for example, is a no-op when `_analyzed_version`
matches the current version. The `_ocr_pages` tag is what the
`state-after-write/ocr.md` fragment guards on to decide whether OCR needs
to run.

## Focus / anchor / lane (deep-find projection)

These tags are populated on `find(deep=True)` and other projection results
to record where each result was anchored relative to the search intent.
They are computed at projection time in `keep/projections.py` and attached
to the returned `Item`. They are listed in `INTERNAL_TAGS` so they don't
appear in default display output, but can still be inspected via `--json`.

| Tag | Purpose |
|-----|---------|
| `_focus_part` | Part number that was the focus of the projection |
| `_focus_version` | Version number for the focused result |
| `_focus_summary` | Summary text used for the focused result |
| `_focus_start_line` / `_focus_end_line` | Line range within the part (for source highlighting) |
| `_anchor_id` | The originating note ID that anchored this result in the projection |
| `_anchor_type` | The kind of anchor (e.g. similar, edge, lineage) |
| `_lane` | Search lane the result came from (search, branch, explore) |

Because these are computed at projection time, they only appear on results
returned from a deep-find or projection-aware flow — they are not present
on plain `kp.get()` output.

---

## Protection mechanism

System tags are filtered out of user-supplied tag maps:

```python
# keep/types.py
SYSTEM_TAG_PREFIX = "_"

def filter_non_system_tags(tags: dict[str, Any]) -> TagMap:
    """Strip any tags whose key starts with the system prefix."""
    return {k: v for k, v in tags.items() if not k.startswith(SYSTEM_TAG_PREFIX)}
```

This is called before merging user-provided tags inside `put()` and `tag()`.

A separate `INTERNAL_TAGS` set in the same module lists the system tags that
are *also* hidden from default display output (the focus/anchor family,
plus `_updated_date`/`_accessed_date`). User code can still query them via
`tags={"_internal_tag": "value"}` filters.

## Tag merge order

When indexing documents, tags merge in this order (later wins on collision):

1. **Existing tags** — preserved from the previous version
2. **Config tags** — from `[tags]` in `keep.toml`
3. **Environment tags** — from `KEEP_TAG_*` variables
4. **User tags** — passed to `put()` or `tag()`
5. **System tags** — added/updated by the system (cannot be overridden)

## Querying by system tags

```python
# Inline items
inline_items = kp.list_items(tags={"_source": "inline"})

# Items indexed today
today = kp.list_items(tags={"_updated_date": "2026-04-08"})

# Items already analyzed at version 4 or above
analyzed = kp.list_items(tags={"_analyzed_version": "4"})
```

CLI:

```bash
keep list -t _source=inline
keep list -t _updated_date=2026-04-08
keep list -a                  # Include system docs (dot-prefix IDs)
```

System documents are identified by their dot-prefix ID (`.now`, `.tag/*`,
`.state/*`, `.prompt/*`, `.meta/*`) and are excluded from default listings.
Use `--all` (`-a`) to include them.

## Versioning and system tags

When a document is updated, the previous version (with all of its system
tags) is archived in `document_versions`. This preserves the complete tag
state at every point in history.

```python
current = kp.get("doc:1")
print(current.tags["_updated"])     # "2026-04-08T14:45:00"

prev = kp.get_version("doc:1", offset=1)
print(prev.tags["_updated"])        # "2026-04-07T10:30:00"
```

## See Also

- [TAGGING.md](TAGGING.md) — User-managed tags, constrained values, filtering
- [META-TAGS.md](META-TAGS.md) — Contextual queries (`.meta/*`)
- [EDGE-TAGS.md](EDGE-TAGS.md) — Tags as navigable relationships
- [REFERENCE.md](REFERENCE.md) — CLI quick reference
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture
