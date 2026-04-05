# One-Line Format Templates

Unify all single-line note rendering into a template system with named formats.

## Problem

15+ call sites across `projections.py`, `cli.py`, `thin_cli.py`, and
`render_flow_response` each format note references differently:

| Section | Current Format |
|---------|---------------|
| Search results (projections) | `- id (0.85)  [2026-04-05]  summary` |
| Similar (context) | `  - id (0.88) 2026-04-05 summary` |
| Meta | `  - id summary` (no date, no score) |
| Edges | `  predicate: id summary` (no date) |
| Parts | `  - @P{3} summary` |
| Versions | `  - @V{1} 2026-04-04 summary` |
| CLI list | `id (0.95) 2026-04-05 summary` |
| Binding meta | `  - id summary[:120]` |
| Binding edges | `  predicate: id summary[:100]` |

Date handling is also inconsistent:
- `projections.py` uses raw ISO `[:10]` (not localized)
- `cli.py` and `thin_cli.py` use `local_date()` (localized)
- Some sections omit dates entirely
- Text is sometimes `note_display_name()`, sometimes raw `summary[:N]`

## Design

### Template Variables

Every one-line renderer produces the same set of variables:

| Variable | Type | Source | Example |
|----------|------|--------|---------|
| `{id}` | str | Note ID, possibly with `@V{N}` or `@P{N}` suffix | `contact:telegram:42` |
| `{score}` | str | Formatted similarity score, or empty | `0.85` |
| `{date}` | str | `local_date(_updated or _created)` — always YYYY-MM-DD | `2026-04-05` |
| `{updated}` | str | Full ISO timestamp from `_updated` tag | `2026-04-05T11:22:33Z` |
| `{text}` | str | `note_display_name(tags, summary, max_len=N)` | `Alice Smith` |
| `{predicate}` | str | Edge predicate name (edges only) | `speaker` |

### Named Formats

Formats are **callable** — functions with a common signature, not
f-string templates. This allows proper JSON serialization, field
omission, and format-specific logic.

```python
# keep/formats.py
import json
from .types import note_display_name, local_date

# Common signature for all format functions:
#   (id, *, score, tags, summary, predicate, max_text, indent) -> str

def oneline_legacy(
    id: str, *, score: float | None = None,
    tags: dict | None = None, summary: str = "",
    predicate: str = "", max_text: int = 80,
    indent: str = "",
) -> str:
    """Current format: `- id (0.85)  [2026-04-05]  display name`"""
    tags = tags or {}
    text = note_display_name(tags, summary, max_len=max_text)
    date = local_date(tags.get("_updated") or tags.get("_created", ""))
    parts = [f"{indent}- {id}"]
    if score is not None:
        parts.append(f"({score:.2f})")
    if date:
        parts.append(f" [{date}]")
    parts.append(f" {text}")
    return "".join(parts)


def oneline_structured(
    id: str, *, score: float | None = None,
    tags: dict | None = None, summary: str = "",
    predicate: str = "", max_text: int = 80,
    indent: str = "",
) -> str:
    """Structured format: `id: {"score": 0.85, "updated": "...", "text": "..."}`

    Proper JSON values — escaped text, null-free (missing fields omitted),
    consistent decimal places on score, full ISO timestamps.
    """
    tags = tags or {}
    text = note_display_name(tags, summary, max_len=max_text)
    updated = tags.get("_updated") or tags.get("_created", "")
    obj: dict = {}
    if score is not None:
        obj["score"] = round(score, 2)
    if updated:
        obj["updated"] = updated
    obj["text"] = text
    label = f"{predicate}: {id}" if predicate else id
    return f"{indent}{label}: {json.dumps(obj, ensure_ascii=False)}"


# Registry of named formats
FORMATS = {
    "legacy": oneline_legacy,
    "structured": oneline_structured,
}

# Active default — start with legacy, switch to structured once migrated
format_oneline = oneline_legacy
```

### Call Sites to Migrate

All ~15 renderers converge on `format_oneline()`:

1. **`cli.py:_render_edge_ref_value`** — edge tags in YAML frontmatter (singular and array)
2. **`projections.py:plan_find_context_render`** — primary search results + deep anchors
2. **`projections.py`** — thread versions, detail versions, detail parts
3. **`cli.py:_format_summary_line`** — CLI list output
4. **`cli.py:_render_binding`** — meta sections, edges
5. **`cli.py:_render_item_context`** — similar, meta, edges, parts, prev/next
6. **`cli.py:render_flow_response`** — flow versions, flow edges
7. **`thin_cli.py:_render_item_line`** — thin CLI search results
8. **`thin_cli.py:_render_context`** — similar, meta, edges, parts, prev/next

### Budget Accounting

`projections.py` currently uses `_tok(line)` for budget calculations.
After this change, budget accounting must use the same formatter output
so token estimates match rendered output. The existing `budget_line`
pattern (using full summary for cost, display name for render) should
be preserved — `format_oneline` returns the display line, and the
budget cost is computed from the same line.

### Sections That Don't Have All Fields

- **Meta**: no score, no date — template skips empty fields
- **Edges**: has predicate, no score — `{predicate}: {id} {text}`
- **Parts**: id is `@P{N}`, no score, no date
- **Versions**: id is `@V{N}`, no score, has date

No `_clean_empty_fields` needed — each format function builds only
the fields it has. Missing data is simply not emitted.

### Structured Format Examples

`json.dumps` handles escaping. Missing fields are omitted (no nulls):

```
# Search result (score + date):
learning-edges: {"score": 0.85, "updated": "2026-04-05T11:22:33Z", "text": "Edge tags create relationships"}

# Meta item (no score, no date):
learning-edges: {"text": "Edge tags create relationships"}

# Edge (with predicate):
speaker: contact:telegram:42: {"text": "Alice"}

# Version:
@V{3}: {"updated": "2026-04-04T09:15:00Z", "text": "Previous version summary"}

# Text with quotes (properly escaped):
email-thread: {"score": 0.72, "text": "Re: \"Project Alpha\" kickoff notes"}
```

### Full `get`-Context Example

Current (legacy) output for `get learning-edges`:

```yaml
---
id: learning-edges
tags:
  _accessed: "2026-04-05T21:06:29"
  _content_length: "81"
  _created: "2026-04-05T21:06:29"
  _source: "inline"
  _updated: "2026-04-05T21:06:29"
  informed_by: learning-autovivify [2026-04-05] "Auto-vivify creates target notes from edge refs."
  referenced_by:
    - note-a [2026-04-05] "First reference"
    - note-b "Second reference"
  speaker: contact:telegram:42 "Alice"
  topic: "edges"
  type: "learning"
similar:
  - learning-edges      (0.95) 2026-04-05 Edge tags create graph relationships with inverse edges.
  - learning-autovivify (0.84) 2026-04-05 Auto-vivify creates target notes from edge refs.
  - contact:telegram:42 (0.81) 2026-04-05 Alice is a developer working on keep.
meta/learnings:
  - learning-edges      Edge tags create graph relationships with inverse edges.
  - learning-autovivify Auto-vivify creates target notes from edge refs.
prev:
  - @V{1} 2026-04-05 Edge tags create graph relationships between notes.
---
Edge tags create graph relationships with inverse edges.
```

Same note in structured format:

```yaml
---
id: learning-edges
tags:
  _accessed: "2026-04-05T21:06:29"
  _content_length: "81"
  _created: "2026-04-05T21:06:29"
  _source: "inline"
  _updated: "2026-04-05T21:06:29"
  informed_by: learning-autovivify: {"updated": "2026-04-05T21:06:29", "text": "Auto-vivify creates target notes from edge refs."}
  referenced_by:
    - note-a: {"updated": "2026-04-05T21:06:29", "text": "First reference"}
    - note-b: {"text": "Second reference"}
  speaker: contact:telegram:42: {"text": "Alice"}
  topic: "edges"
  type: "learning"
similar:
  - learning-edges: {"score": 0.95, "updated": "2026-04-05T21:06:29", "text": "Edge tags create graph relationships with inverse edges."}
  - learning-autovivify: {"score": 0.84, "updated": "2026-04-05T21:06:29", "text": "Auto-vivify creates target notes from edge refs."}
  - contact:telegram:42: {"score": 0.81, "updated": "2026-04-05T21:06:29", "text": "Alice"}
meta/learnings:
  - learning-edges: {"text": "Edge tags create graph relationships with inverse edges."}
  - learning-autovivify: {"text": "Auto-vivify creates target notes from edge refs."}
prev:
  - @V{1}: {"updated": "2026-04-05T21:06:29", "text": "Edge tags create graph relationships between notes."}
---
Edge tags create graph relationships with inverse edges.
```

Edge tags in the frontmatter also use the formatter. Currently
rendered by `_render_edge_ref_value` as `id [date] "summary"`:

```yaml
# Legacy:
  informed_by: learning-autovivify [2026-04-05] "Auto-vivify creates target notes from edge refs."
  speaker: contact:telegram:42 "Alice"
  references:
    - note-a [2026-04-05] "First reference"
    - note-b "Second reference"

# Structured:
  informed_by: learning-autovivify: {"updated": "2026-04-05T21:06:29", "text": "Auto-vivify creates target notes from edge refs."}
  speaker: contact:telegram:42: {"text": "Alice"}
  references:
    - note-a: {"updated": "2026-04-05T21:06:29", "text": "First reference"}
    - note-b: {"text": "Second reference"}
```

Notes:
- The YAML frontmatter (id, non-edge tags) and body are unchanged
- Each section uses the same structured formatter but with different available fields
- `similar` has score + updated + text
- `meta` has text only (no score, no date)
- `prev`/`next` have updated + text (no score)
- `edges` (when rendered as a section) would use `predicate: id: {"text": "..."}` format

### Migration Plan

1. Add `keep/formats.py` with `format_oneline`, named templates, and `_clean_empty_fields`
2. Add tests for all template variations and edge cases
3. Migrate each call site one at a time, verifying existing tests pass
4. Start with `ONELINE_LEGACY` as default to preserve behavior
5. Switch default to `ONELINE_DEFAULT` once all sites are migrated
6. Update projection budget tests for new format lengths

### Future

- Additional named formats (e.g. compact for constrained contexts)
- User-configurable format via store config or CLI flag
- Per-section format overrides (e.g. compact for deep anchors)
- YAML/JSON output modes for programmatic consumers
