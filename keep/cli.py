"""
CLI interface for reflective memory.

Usage:
    keep find "query text"
    keep put file:///path/to/doc.md
    keep get file:///path/to/doc.md
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

# Pattern for version identifier suffix: @V{N} where N is digits only
VERSION_SUFFIX_PATTERN = re.compile(r'@V\{(\d+)\}$')

# URI scheme pattern per RFC 3986: scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
# Used to distinguish URIs from plain text in the update command
_URI_SCHEME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://')

from .api import Keeper, _text_content_id
from .config import get_tool_directory
from .document_store import VersionInfo
from .types import Item
from .logging_config import configure_quiet_mode, enable_debug_mode


# Configure quiet mode by default (suppress verbose library output)
# Set KEEP_VERBOSE=1 to enable debug mode via environment
if os.environ.get("KEEP_VERBOSE") == "1":
    enable_debug_mode()
else:
    configure_quiet_mode(quiet=True)


def _version_callback(value: bool):
    if value:
        from importlib.metadata import version
        print(f"keep {version('keep-skill')}")
        raise typer.Exit()


def _verbose_callback(value: bool):
    if value:
        enable_debug_mode()


# Global state for CLI options
_json_output = False
_ids_output = False
_full_output = False
_store_override: Optional[Path] = None


def _json_callback(value: bool):
    global _json_output
    _json_output = value


def _get_json_output() -> bool:
    return _json_output


def _ids_callback(value: bool):
    global _ids_output
    _ids_output = value


def _get_ids_output() -> bool:
    return _ids_output


def _full_callback(value: bool):
    global _full_output
    _full_output = value


def _get_full_output() -> bool:
    return _full_output


def _store_callback(value: Optional[Path]):
    global _store_override
    if value is not None:
        _store_override = value


def _get_store_override() -> Optional[Path]:
    return _store_override


app = typer.Typer(
    name="keep",
    help="Reflective memory with semantic search.",
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode=None,
)


# Shell-safe character set for IDs (no quoting needed)
_SHELL_SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9_./:@{}\-%]+$')


def _shell_quote_id(id: str) -> str:
    """Quote an ID for safe shell usage if it contains non-shell-safe characters.

    IDs containing only [a-zA-Z0-9_./:@{}%-] are returned as-is.
    Others are wrapped in single quotes with internal single quotes escaped.
    """
    if _SHELL_SAFE_PATTERN.match(id):
        return id
    # Escape any single quotes within the ID: ' → '\''
    escaped = id.replace("'", "'\\''")
    return f"'{escaped}'"


# -----------------------------------------------------------------------------
# Output Formatting
#
# Three output formats, controlled by global flags:
#   --ids:  versioned ID only (id@V{N})
#   --full: YAML frontmatter with tags, similar items, version nav
#   default: summary line (id@V{N} date summary)
#
# JSON output (--json) works with any of the above.
# -----------------------------------------------------------------------------

def _filter_display_tags(tags: dict) -> dict:
    """Filter out internal-only tags for display."""
    from .types import INTERNAL_TAGS
    return {k: v for k, v in tags.items() if k not in INTERNAL_TAGS}


def _format_yaml_frontmatter(
    item: Item,
    version_nav: Optional[dict[str, list[VersionInfo]]] = None,
    viewing_offset: Optional[int] = None,
    similar_items: Optional[list[Item]] = None,
    similar_offsets: Optional[dict[str, int]] = None,
    meta_sections: Optional[dict[str, list[Item]]] = None,
) -> str:
    """
    Format item as YAML frontmatter with summary as content.

    Args:
        item: The item to format
        version_nav: Optional version navigation info (prev/next lists)
        viewing_offset: If viewing an old version, the offset (1=previous, 2=two ago)
        similar_items: Optional list of similar items to display
        similar_offsets: Version offsets for similar items (item.id -> offset)
        meta_sections: Optional dict of {name: [Items]} from meta-doc resolution

    Note: Offset computation (v1, v2, etc.) assumes version_nav lists
    are ordered newest-first, matching list_versions() ordering.
    Changing that ordering would break the vN = -V N correspondence.
    """
    cols = shutil.get_terminal_size((120, 24)).columns

    def _truncate_summary(summary: str, prefix_len: int) -> str:
        """Truncate summary to fit terminal width, matching _format_summary_line."""
        max_width = max(cols - prefix_len, 20)
        s = summary.replace("\n", " ")
        if len(s) > max_width:
            s = s[:max_width - 3].rsplit(" ", 1)[0] + "..."
        return s

    version = viewing_offset if viewing_offset is not None else 0
    version_suffix = f"@V{{{version}}}" if version > 0 else ""
    lines = ["---", f"id: {_shell_quote_id(item.id)}{version_suffix}"]
    display_tags = _filter_display_tags(item.tags)
    if display_tags:
        tag_items = ", ".join(f"{k}: {v}" for k, v in sorted(display_tags.items()))
        lines.append(f"tags: {{{tag_items}}}")
    if item.score is not None:
        lines.append(f"score: {item.score:.3f}")

    # Add similar items if available (version-scoped IDs with date and summary)
    if similar_items:
        # Build ID strings for alignment
        sim_ids = []
        for sim_item in similar_items:
            base_id = sim_item.tags.get("_base_id", sim_item.id)
            offset = (similar_offsets or {}).get(sim_item.id, 0)
            version_suffix = f"@V{{{offset}}}" if offset > 0 else ""
            sim_ids.append(f"{base_id}{version_suffix}")
        id_width = min(max(len(s) for s in sim_ids), 20)
        lines.append("similar:")
        for sim_item, sid in zip(similar_items, sim_ids):
            score_str = f"({sim_item.score:.2f})" if sim_item.score else ""
            date_part = sim_item.tags.get("_updated", sim_item.tags.get("_created", ""))[:10]
            actual_id_len = max(len(sid), id_width)
            prefix_len = 4 + actual_id_len + 1 + len(score_str) + 1 + len(date_part) + 1
            summary_preview = _truncate_summary(sim_item.summary, prefix_len)
            lines.append(f"  - {sid.ljust(id_width)} {score_str} {date_part} {summary_preview}")

    # Add meta-doc sections (tag-based contextual results, prefixed to avoid key conflicts)
    if meta_sections:
        for name, meta_items in meta_sections.items():
            meta_ids = [_shell_quote_id(mi.id) for mi in meta_items]
            id_width = min(max(len(s) for s in meta_ids), 20)
            lines.append(f"meta/{name}:")
            for meta_item, mid in zip(meta_items, meta_ids):
                actual_id_len = max(len(mid), id_width)
                prefix_len = 4 + actual_id_len + 1
                summary_preview = _truncate_summary(meta_item.summary, prefix_len)
                lines.append(f"  - {mid.ljust(id_width)} {summary_preview}")

    # Add version navigation (just @V{N} since ID is shown at top, with date + summary)
    if version_nav:
        # Current offset (0 if viewing current)
        current_offset = viewing_offset if viewing_offset is not None else 0

        if version_nav.get("prev"):
            prev_ids = [f"@V{{{current_offset + i + 1}}}" for i in range(len(version_nav["prev"]))]
            id_width = max(len(s) for s in prev_ids)
            lines.append("prev:")
            for vid, v in zip(prev_ids, version_nav["prev"]):
                date_part = v.created_at[:10] if v.created_at else ""
                prefix_len = 4 + id_width + 1 + len(date_part) + 1
                summary_preview = _truncate_summary(v.summary, prefix_len)
                lines.append(f"  - {vid.ljust(id_width)} {date_part} {summary_preview}")
        if version_nav.get("next"):
            next_ids = [f"@V{{{current_offset - i - 1}}}" for i in range(len(version_nav["next"]))]
            id_width = max(len(s) for s in next_ids)
            lines.append("next:")
            for vid, v in zip(next_ids, version_nav["next"]):
                date_part = v.created_at[:10] if v.created_at else ""
                prefix_len = 4 + id_width + 1 + len(date_part) + 1
                summary_preview = _truncate_summary(v.summary, prefix_len)
                lines.append(f"  - {vid.ljust(id_width)} {date_part} {summary_preview}")
        elif viewing_offset is not None:
            # Viewing old version and next is empty means current is next
            lines.append("next:")
            lines.append(f"  - @V{{0}}")

    lines.append("---")
    lines.append(item.summary)  # Summary IS the content
    return "\n".join(lines)


def _format_summary_line(item: Item, id_width: int = 0) -> str:
    """Format item as single summary line: id date summary (with @V{N} only for old versions)

    Args:
        item: The item to format
        id_width: Minimum width for ID column (for alignment across items)
    """
    # Get version-scoped ID (omit @V{0} for current version)
    base_id = item.tags.get("_base_id", item.id)
    version = item.tags.get("_version", "0")
    version_suffix = f"@V{{{version}}}" if version != "0" else ""
    versioned_id = f"{_shell_quote_id(base_id)}{version_suffix}"

    # Pad ID for column alignment
    padded_id = versioned_id.ljust(id_width) if id_width else versioned_id

    # Get date (from _updated_date or _updated or _created)
    date = item.tags.get("_updated_date") or item.tags.get("_updated", "")[:10] or item.tags.get("_created", "")[:10] or ""

    # Truncate summary to fit terminal width, collapse newlines
    cols = shutil.get_terminal_size((120, 24)).columns
    prefix_len = len(padded_id) + 1 + len(date) + 1  # "id date "
    max_summary = max(cols - prefix_len, 20)
    summary = item.summary.replace("\n", " ")
    if len(summary) > max_summary:
        summary = summary[:max_summary - 3].rsplit(" ", 1)[0] + "..."

    return f"{padded_id} {date} {summary}"


def _format_versioned_id(item: Item) -> str:
    """Format item ID with version suffix only for old versions: id or id@V{N}"""
    base_id = item.tags.get("_base_id", item.id)
    version = item.tags.get("_version", "0")
    version_suffix = f"@V{{{version}}}" if version != "0" else ""
    return f"{_shell_quote_id(base_id)}{version_suffix}"


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option(
        "--verbose", "-v",
        help="Enable debug-level logging to stderr",
        callback=_verbose_callback,
        is_eager=True,
    )] = False,
    output_json: Annotated[bool, typer.Option(
        "--json", "-j",
        help="Output as JSON",
        callback=_json_callback,
        is_eager=True,
    )] = False,
    ids_only: Annotated[bool, typer.Option(
        "--ids", "-I",
        help="Output only IDs (for piping to xargs)",
        callback=_ids_callback,
        is_eager=True,
    )] = False,
    full_output: Annotated[bool, typer.Option(
        "--full", "-F",
        help="Output full items (overrides --ids)",
        callback=_full_callback,
        is_eager=True,
    )] = False,
    version: Annotated[Optional[bool], typer.Option(
        "--version",
        help="Show version and exit",
        callback=_version_callback,
        is_eager=True,
    )] = None,
    store: Annotated[Optional[Path], typer.Option(
        "--store", "-s",
        envvar="KEEP_STORE_PATH",
        help="Path to the store directory",
        callback=_store_callback,
        is_eager=True,
    )] = None,
):
    """Reflective memory with semantic search."""
    # If no subcommand provided, show the current intentions (now)
    if ctx.invoked_subcommand is None:
        from .api import NOWDOC_ID
        kp = _get_keeper(None, "default")
        item = kp.get_now()
        version_nav = kp.get_version_nav(NOWDOC_ID, None, collection="default")
        similar_items = kp.get_similar_for_display(NOWDOC_ID, limit=3, collection="default")
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(NOWDOC_ID, collection="default")
        typer.echo(_format_item(
            item,
            as_json=_get_json_output(),
            version_nav=version_nav,
            similar_items=similar_items,
            similar_offsets=similar_offsets,
            meta_sections=meta_sections,
        ))


# -----------------------------------------------------------------------------
# Common Options
# -----------------------------------------------------------------------------

StoreOption = Annotated[
    Optional[Path],
    typer.Option(
        "--store", "-s",
        envvar="KEEP_STORE_PATH",
        help="Path to the store directory (default: ~/.keep/)"
    )
]

CollectionOption = Annotated[
    str,
    typer.Option(
        "--collection", "-c",
        envvar="KEEP_COLLECTION",
        help="Collection name"
    )
]

LimitOption = Annotated[
    int,
    typer.Option(
        "--limit", "-n",
        help="Maximum results to return"
    )
]


SinceOption = Annotated[
    Optional[str],
    typer.Option(
        "--since",
        help="Only items updated since (ISO duration: P3D, P1W, PT1H; or date: 2026-01-15)"
    )
]


def _format_item(
    item: Item,
    as_json: bool = False,
    version_nav: Optional[dict[str, list[VersionInfo]]] = None,
    viewing_offset: Optional[int] = None,
    similar_items: Optional[list[Item]] = None,
    similar_offsets: Optional[dict[str, int]] = None,
    meta_sections: Optional[dict[str, list[Item]]] = None,
) -> str:
    """
    Format a single item for display.

    Output selection:
      --ids: versioned ID only
      --full or version_nav/similar_items present: YAML frontmatter
      default: summary line (id@V{N} date summary)

    Args:
        item: The item to format
        as_json: Output as JSON
        version_nav: Version navigation info (triggers full format)
        viewing_offset: Version offset if viewing old version (triggers full format)
        similar_items: Similar items to display (triggers full format)
        similar_offsets: Version offsets for similar items
        meta_sections: Meta-doc resolved sections {name: [Items]}
    """
    if _get_ids_output():
        versioned_id = _format_versioned_id(item)
        return json.dumps(versioned_id) if as_json else versioned_id

    if as_json:
        result = {
            "id": item.id,
            "summary": item.summary,
            "tags": _filter_display_tags(item.tags),
            "score": item.score,
        }
        if viewing_offset is not None:
            result["version"] = viewing_offset
            result["vid"] = f"{item.id}@V{{{viewing_offset}}}"
        if similar_items:
            result["similar"] = [
                {
                    "id": f"{s.tags.get('_base_id', s.id)}@V{{{(similar_offsets or {}).get(s.id, 0)}}}",
                    "score": s.score,
                    "date": s.tags.get("_updated", s.tags.get("_created", ""))[:10],
                    "summary": s.summary[:60],
                }
                for s in similar_items
            ]
        if meta_sections:
            result["meta"] = {
                name: [
                    {"id": mi.id, "summary": mi.summary[:60]}
                    for mi in items
                ]
                for name, items in meta_sections.items()
            }
        if version_nav:
            current_offset = viewing_offset if viewing_offset is not None else 0
            result["version_nav"] = {}
            if version_nav.get("prev"):
                result["version_nav"]["prev"] = [
                    {
                        "offset": current_offset + i + 1,
                        "vid": f"{item.id}@V{{{current_offset + i + 1}}}",
                        "created_at": v.created_at,
                        "summary": v.summary[:60],
                    }
                    for i, v in enumerate(version_nav["prev"])
                ]
            if version_nav.get("next"):
                result["version_nav"]["next"] = [
                    {
                        "offset": current_offset - i - 1,
                        "vid": f"{item.id}@V{{{current_offset - i - 1}}}",
                        "created_at": v.created_at,
                        "summary": v.summary[:60],
                    }
                    for i, v in enumerate(version_nav["next"])
                ]
            elif viewing_offset is not None:
                result["version_nav"]["next"] = [{"offset": 0, "vid": f"{item.id}@V{{0}}", "label": "current"}]
        return json.dumps(result)

    # Full format when:
    # - --full flag is set
    # - version navigation or similar items are provided (can't display in summary)
    if _get_full_output() or version_nav or similar_items or viewing_offset is not None or meta_sections:
        return _format_yaml_frontmatter(item, version_nav, viewing_offset, similar_items, similar_offsets, meta_sections)
    return _format_summary_line(item)


def _format_items(items: list[Item], as_json: bool = False) -> str:
    """Format multiple items for display."""
    if _get_ids_output():
        ids = [_format_versioned_id(item) for item in items]
        return json.dumps(ids) if as_json else "\n".join(ids)

    if as_json:
        return json.dumps([
            {
                "id": item.id,
                "summary": item.summary,
                "tags": _filter_display_tags(item.tags),
                "score": item.score,
            }
            for item in items
        ], indent=2)

    if not items:
        return "No results."

    # Full format: YAML frontmatter with double-newline separator
    # Default: summary lines with single-newline separator
    if _get_full_output():
        return "\n\n".join(_format_yaml_frontmatter(item) for item in items)

    # Compute ID column width for alignment (capped to avoid long URIs dominating)
    max_id = max(len(_format_versioned_id(item)) for item in items)
    id_width = min(max_id, 20)
    return "\n".join(_format_summary_line(item, id_width) for item in items)


NO_PROVIDER_ERROR = """
No embedding provider configured.

To use keep, configure a provider:

  API-based (recommended):
    export VOYAGE_API_KEY=...      # Get at dash.voyageai.com
    export ANTHROPIC_API_KEY=...   # Optional: for better summaries

  Local (macOS Apple Silicon):
    pip install 'keep-skill[local]'

See: https://github.com/hughpyle/keep#installation
"""


def _get_keeper(store: Optional[Path], collection: str) -> Keeper:
    """Initialize memory, handling errors gracefully."""
    import atexit

    # Check global override from --store on main command
    actual_store = store if store is not None else _get_store_override()
    try:
        kp = Keeper(actual_store, collection=collection)
        # Ensure close() runs before interpreter shutdown to release model locks
        atexit.register(kp.close)
        # Check for missing embedding provider
        if kp._config and kp._config.embedding is None:
            typer.echo(NO_PROVIDER_ERROR.strip(), err=True)
            raise typer.Exit(1)
        # Check tool integrations (fast path: dict lookup, no I/O)
        if kp._config:
            from .integrations import check_and_install
            try:
                check_and_install(kp._config)
            except Exception:
                pass  # Never block normal operation
        return kp
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


def _parse_tags(tags: Optional[list[str]]) -> dict[str, str]:
    """Parse key=value tag list to dict."""
    if not tags:
        return {}
    parsed = {}
    for tag in tags:
        if "=" not in tag:
            typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
            raise typer.Exit(1)
        k, v = tag.split("=", 1)
        parsed[k] = v
    return parsed


def _filter_by_tags(items: list, tags: list[str]) -> list:
    """
    Filter items by tag specifications (AND logic).

    Each tag can be:
    - "key" - item must have this tag key (any value)
    - "key=value" - item must have this exact tag
    """
    if not tags:
        return items

    result = items
    for t in tags:
        if "=" in t:
            key, value = t.split("=", 1)
            result = [item for item in result if item.tags.get(key) == value]
        else:
            # Key only - check if key exists
            result = [item for item in result if t in item.tags]
    return result


def _parse_frontmatter(text: str) -> tuple[str, dict[str, str]]:
    """Parse YAML frontmatter from text, return (content, tags)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            import yaml
            frontmatter = yaml.safe_load(parts[1])
            content = parts[2].lstrip("\n")
            tags = frontmatter.get("tags", {}) if frontmatter else {}
            return content, {k: str(v) for k, v in tags.items()}
    return text, {}


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

@app.command()
def find(
    query: Annotated[Optional[str], typer.Argument(help="Search query text")] = None,
    id: Annotated[Optional[str], typer.Option(
        "--id",
        help="Find items similar to this ID (instead of text search)"
    )] = None,
    include_self: Annotated[bool, typer.Option(
        help="Include the queried item (only with --id)"
    )] = False,
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Filter by tag (key or key=value, repeatable)"
    )] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    since: SinceOption = None,
):
    """
    Find items using semantic similarity search.

    \b
    Examples:
        keep find "authentication"              # Search by text
        keep find --id file:///path/to/doc.md   # Find similar to item
        keep find "auth" -t project=myapp       # Search + filter by tag
    """
    if id and query:
        typer.echo("Error: Specify either a query or --id, not both", err=True)
        raise typer.Exit(1)
    if not id and not query:
        typer.echo("Error: Specify a query or --id", err=True)
        raise typer.Exit(1)

    kp = _get_keeper(store, collection)

    # Search with higher limit if filtering, then post-filter
    search_limit = limit * 5 if tag else limit

    if id:
        results = kp.find_similar(id, limit=search_limit, since=since, include_self=include_self)
    else:
        results = kp.find(query, limit=search_limit, since=since)

    # Post-filter by tags if specified
    if tag:
        results = _filter_by_tags(results, tag)

    typer.echo(_format_items(results[:limit], as_json=_get_json_output()))


@app.command()
def search(
    query: Annotated[str, typer.Argument(default=..., help="Full-text search query")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    since: SinceOption = None,
):
    """
    Search item summaries using full-text search.
    """
    kp = _get_keeper(store, collection)
    results = kp.query_fulltext(query, limit=limit, since=since)
    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command("list")
def list_recent(
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Filter by tag (key or key=value, repeatable)"
    )] = None,
    tags: Annotated[Optional[str], typer.Option(
        "--tags", "-T",
        help="List tag keys (--tags=), or values for KEY (--tags=KEY)"
    )] = None,
    sort: Annotated[str, typer.Option(
        "--sort",
        help="Sort order: 'updated' (default) or 'accessed'"
    )] = "updated",
    since: SinceOption = None,
):
    """
    List recent items, filter by tags, or list tag keys/values.

    \b
    Examples:
        keep list                      # Recent items (by update time)
        keep list --sort accessed      # Recent items (by access time)
        keep list --tag foo            # Items with tag 'foo' (any value)
        keep list --tag foo=bar        # Items with tag foo=bar
        keep list --tag foo --tag bar  # Items with both tags
        keep list --tags=              # List all tag keys
        keep list --tags=foo           # List values for tag 'foo'
        keep list --since P3D          # Items updated in last 3 days
    """
    kp = _get_keeper(store, collection)

    # --tags mode: list keys or values
    if tags is not None:
        # Empty string means list all keys, otherwise list values for key
        key = tags if tags else None
        values = kp.list_tags(key, collection=collection)
        if _get_json_output():
            typer.echo(json.dumps(values))
        else:
            if not values:
                if key:
                    typer.echo(f"No values for tag '{key}'.")
                else:
                    typer.echo("No tags found.")
            else:
                for v in values:
                    typer.echo(v)
        return

    # --tag mode: filter items by tag(s)
    if tag:
        # Parse each tag as key or key=value
        # Multiple tags require all to match (AND)
        results = None
        for t in tag:
            if "=" in t:
                key, value = t.split("=", 1)
                matches = kp.query_tag(key, value, limit=limit, since=since, collection=collection)
            else:
                # Key only - find items with this tag key (any value)
                matches = kp.query_tag(t, limit=limit, since=since, collection=collection)

            if results is None:
                results = {item.id: item for item in matches}
            else:
                # Intersect with previous results
                match_ids = {item.id for item in matches}
                results = {id: item for id, item in results.items() if id in match_ids}

        items = list(results.values()) if results else []
        typer.echo(_format_items(items[:limit], as_json=_get_json_output()))
        return

    # Default: recent items
    results = kp.list_recent(limit=limit, since=since, order_by=sort, collection=collection)
    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command("tag-update")
def tag_update(
    ids: Annotated[list[str], typer.Argument(default=..., help="Document IDs to tag")],
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Tag as key=value (empty value removes: key=)"
    )] = None,
    remove: Annotated[Optional[list[str]], typer.Option(
        "--remove", "-r",
        help="Tag keys to remove"
    )] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Add, update, or remove tags on existing documents.

    Does not re-process the document - only updates tags.

    \b
    Examples:
        keep tag-update doc:1 --tag project=myapp
        keep tag-update doc:1 doc:2 --tag status=reviewed
        keep tag-update doc:1 --remove obsolete_tag
        keep tag-update doc:1 --tag temp=  # Remove via empty value
    """
    kp = _get_keeper(store, collection)

    # Parse tags from key=value format
    tag_changes = _parse_tags(tags)

    # Add explicit removals as empty strings
    if remove:
        for key in remove:
            tag_changes[key] = ""

    if not tag_changes:
        typer.echo("Error: Specify at least one --tag or --remove", err=True)
        raise typer.Exit(1)

    # Process each document
    results = []
    for doc_id in ids:
        item = kp.tag(doc_id, tags=tag_changes, collection=collection)
        if item is None:
            typer.echo(f"Not found: {doc_id}", err=True)
        else:
            results.append(item)

    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command("put")
def put(
    source: Annotated[Optional[str], typer.Argument(
        help="URI to fetch, text content, or '-' for stdin"
    )] = None,
    id: Annotated[Optional[str], typer.Option(
        "--id", "-i",
        help="Document ID (auto-generated for text/stdin modes)"
    )] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Tag as key=value (can be repeated)"
    )] = None,
    summary: Annotated[Optional[str], typer.Option(
        "--summary",
        help="User-provided summary (skips auto-summarization)"
    )] = None,
    suggest_tags: Annotated[bool, typer.Option(
        "--suggest-tags",
        help="Show tag suggestions from similar items"
    )] = False,
):
    """
    Add or update a document in the store.

    \b
    Three input modes (auto-detected):
      keep put file:///path       # URI mode: has ://
      keep put "my note"          # Text mode: content-addressed ID
      keep put -                  # Stdin mode: explicit -
      echo "pipe" | keep put      # Stdin mode: piped input

    \b
    Text mode uses content-addressed IDs for versioning:
      keep put "my note"           # Creates %{hash}
      keep put "my note" -t done   # Same ID, new version (tag change)
      keep put "different note"    # Different ID (new doc)
    """
    kp = _get_keeper(store, collection)
    parsed_tags = _parse_tags(tags)

    # Determine mode based on source content
    if source == "-" or (source is None and not sys.stdin.isatty()):
        # Stdin mode: explicit '-' or piped input
        content = sys.stdin.read()
        content, frontmatter_tags = _parse_frontmatter(content)
        parsed_tags = {**frontmatter_tags, **parsed_tags}  # CLI tags override
        if summary is not None:
            typer.echo("Error: --summary cannot be used with stdin input (original content would be lost)", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file --summary '...'", err=True)
            raise typer.Exit(1)
        max_len = kp._config.max_summary_length
        if len(content) > max_len:
            typer.echo(f"Error: stdin content too long to store inline ({len(content)} chars, max {max_len})", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file", err=True)
            raise typer.Exit(1)
        # Use content-addressed ID for stdin text (enables versioning)
        doc_id = id or _text_content_id(content)
        item = kp.remember(content, id=doc_id, tags=parsed_tags or None)
    elif source and _URI_SCHEME_PATTERN.match(source):
        # URI mode: fetch from URI (ID is the URI itself)
        item = kp.update(source, tags=parsed_tags or None, summary=summary)
    elif source:
        # Text mode: inline content (no :// in source)
        if summary is not None:
            typer.echo("Error: --summary cannot be used with inline text (original content would be lost)", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file --summary '...'", err=True)
            raise typer.Exit(1)
        max_len = kp._config.max_summary_length
        if len(source) > max_len:
            typer.echo(f"Error: inline text too long to store ({len(source)} chars, max {max_len})", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file", err=True)
            raise typer.Exit(1)
        # Use content-addressed ID for text (enables versioning)
        doc_id = id or _text_content_id(source)
        item = kp.remember(source, id=doc_id, tags=parsed_tags or None)
    else:
        typer.echo("Error: Provide content, URI, or '-' for stdin", err=True)
        raise typer.Exit(1)

    # Surface similar items (occasion for reflection)
    suggest_limit = 10 if suggest_tags else 3
    similar_items = kp.get_similar_for_display(item.id, limit=suggest_limit, collection=collection)
    similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}

    typer.echo(_format_item(
        item,
        as_json=_get_json_output(),
        similar_items=similar_items[:3] if similar_items else None,
        similar_offsets=similar_offsets if similar_items else None,
    ))

    # Show tag suggestions from similar items
    if suggest_tags and similar_items:
        tag_counts: dict[str, int] = {}
        for si in similar_items:
            for k, v in si.tags.items():
                if k.startswith("_"):
                    continue
                tag = f"{k}={v}" if v else k
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag_counts:
            # Sort by frequency (descending), then alphabetically
            sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
            typer.echo("\nsuggested tags:")
            for tag, count in sorted_tags:
                typer.echo(f"  -t {tag}  ({count})")
            typer.echo(f"\napply with: keep tag-update {_shell_quote_id(item.id)} -t TAG")


@app.command("update", hidden=True)
def update(
    source: Annotated[Optional[str], typer.Argument(help="URI to fetch, text content, or '-' for stdin")] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option("--tag", "-t")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
):
    """Add or update a document (alias for 'put')."""
    put(source=source, id=id, store=store, collection=collection, tags=tags, summary=summary)


@app.command("add", hidden=True)
def add(
    source: Annotated[Optional[str], typer.Argument(help="URI to fetch, text content, or '-' for stdin")] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option("--tag", "-t")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
):
    """Add a document (alias for 'put')."""
    put(source=source, id=id, store=store, collection=collection, tags=tags, summary=summary)


@app.command()
def now(
    content: Annotated[Optional[str], typer.Argument(
        help="Content to set (omit to show current)"
    )] = None,
    file: Annotated[Optional[Path], typer.Option(
        "--file", "-f",
        help="Read content from file"
    )] = None,
    reset: Annotated[bool, typer.Option(
        "--reset",
        help="Reset to default from system"
    )] = False,
    version: Annotated[Optional[int], typer.Option(
        "--version", "-V",
        help="Get specific version (0=current, 1=previous, etc.)"
    )] = None,
    history: Annotated[bool, typer.Option(
        "--history", "-H",
        help="List all versions"
    )] = False,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Set tag (with content) or filter (without content)"
    )] = None,
):
    """
    Get or set the current working intentions.

    With no arguments, displays the current intentions.
    With content, replaces it.

    \b
    Tags behave differently based on mode:
    - With content: -t sets tags on the update
    - Without content: -t filters version history

    \b
    Examples:
        keep now                         # Show current intentions
        keep now "What's important now"  # Update intentions
        keep now "Auth work" -t project=myapp  # Update with tag
        keep now -t project=myapp        # Find version with tag
        keep now -f context.md           # Read content from file
        keep now --reset                 # Reset to default from system
        keep now -V 1                    # Previous version
        keep now --history               # List all versions
    """
    from .api import NOWDOC_ID

    kp = _get_keeper(store, collection)

    # Handle history listing
    if history:
        versions = kp.list_versions(NOWDOC_ID, limit=50, collection=collection)
        current = kp.get(NOWDOC_ID, collection=collection)

        if _get_ids_output():
            # Output version identifiers, one per line
            if current:
                typer.echo(f"{NOWDOC_ID}@V{{0}}")
            for i in range(1, len(versions) + 1):
                typer.echo(f"{NOWDOC_ID}@V{{{i}}}")
        elif _get_json_output():
            result = {
                "id": NOWDOC_ID,
                "current": {
                    "summary": current.summary if current else None,
                    "offset": 0,
                    "vid": f"{NOWDOC_ID}@V{{0}}",
                } if current else None,
                "versions": [
                    {
                        "offset": i + 1,
                        "vid": f"{NOWDOC_ID}@V{{{i + 1}}}",
                        "version": v.version,
                        "summary": v.summary[:60],
                        "created_at": v.created_at,
                    }
                    for i, v in enumerate(versions)
                ],
            }
            typer.echo(json.dumps(result, indent=2))
        else:
            if current:
                summary_preview = current.summary[:60].replace("\n", " ")
                if len(current.summary) > 60:
                    summary_preview += "..."
                typer.echo(f"v0 (current): {summary_preview}")
            if versions:
                typer.echo(f"\nArchived:")
                for i, v in enumerate(versions, start=1):
                    date_part = v.created_at[:10] if v.created_at else "unknown"
                    summary_preview = v.summary[:50].replace("\n", " ")
                    if len(v.summary) > 50:
                        summary_preview += "..."
                    typer.echo(f"  v{i} ({date_part}): {summary_preview}")
            else:
                typer.echo("No version history.")
        return

    # Handle version retrieval
    if version is not None:
        offset = version
        if offset == 0:
            item = kp.get_now()
            internal_version = None
        else:
            item = kp.get_version(NOWDOC_ID, offset, collection=collection)
            # Get internal version number for API call
            versions = kp.list_versions(NOWDOC_ID, limit=1, collection=collection)
            if versions:
                internal_version = versions[0].version - (offset - 1)
            else:
                internal_version = None

        if item is None:
            typer.echo(f"Version not found (offset {offset})", err=True)
            raise typer.Exit(1)

        version_nav = kp.get_version_nav(NOWDOC_ID, internal_version, collection=collection)
        typer.echo(_format_item(
            item,
            as_json=_get_json_output(),
            version_nav=version_nav,
            viewing_offset=offset if offset > 0 else None,
        ))
        return

    # Determine if we're getting or setting
    setting = content is not None or file is not None or reset

    if setting:
        if reset:
            # Reset to default from system (delete first to clear old tags)
            from .api import _load_frontmatter, SYSTEM_DOC_DIR
            kp.delete(NOWDOC_ID)
            try:
                new_content, default_tags = _load_frontmatter(SYSTEM_DOC_DIR / "now.md")
                parsed_tags = default_tags
            except FileNotFoundError:
                typer.echo("Error: Builtin now.md not found", err=True)
                raise typer.Exit(1)
        elif file is not None:
            if not file.exists():
                typer.echo(f"Error: File not found: {file}", err=True)
                raise typer.Exit(1)
            new_content = file.read_text()
            parsed_tags = {}
        else:
            new_content = content
            parsed_tags = {}

        # Parse user-provided tags (merge with default if reset)
        parsed_tags.update(_parse_tags(tags))

        item = kp.set_now(new_content, tags=parsed_tags or None)

        # Surface similar items and meta sections (occasion for reflection)
        similar_items = kp.get_similar_for_display(item.id, limit=3, collection=collection)
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(item.id, collection=collection)

        typer.echo(_format_item(
            item,
            as_json=_get_json_output(),
            similar_items=similar_items if similar_items else None,
            similar_offsets=similar_offsets if similar_items else None,
            meta_sections=meta_sections if meta_sections else None,
        ))
        if not _get_json_output() and not _get_ids_output() and not sys.stdout.isatty():
            typer.echo("\nAs an agent, you should reflect on this work. (run: `keep reflect`)")
    else:
        # Get current intentions (or search version history if tags specified)
        if tags:
            # Search version history for most recent version with matching tags
            item = _find_now_version_by_tags(kp, tags, collection)
            if item is None:
                typer.echo("No version found matching tags", err=True)
                raise typer.Exit(1)
            # No version nav or similar items for filtered results
            typer.echo(_format_item(item, as_json=_get_json_output()))
        else:
            # Standard: get current with version navigation and similar items
            item = kp.get_now()
            version_nav = kp.get_version_nav(NOWDOC_ID, None, collection=collection)
            similar_items = kp.get_similar_for_display(NOWDOC_ID, limit=3, collection=collection)
            similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
            meta_sections = kp.resolve_meta(NOWDOC_ID, collection=collection)
            typer.echo(_format_item(
                item,
                as_json=_get_json_output(),
                version_nav=version_nav,
                similar_items=similar_items,
                similar_offsets=similar_offsets,
                meta_sections=meta_sections,
            ))
            if not _get_json_output() and not _get_ids_output() and not sys.stdout.isatty():
                typer.echo("\nAs an agent, you should reflect on this work. (run: `keep reflect`)")


def _find_now_version_by_tags(kp, tags: list[str], collection: str):
    """
    Search nowdoc version history for most recent version matching all tags.

    Checks current version first, then scans archived versions.
    """
    from .api import NOWDOC_ID

    # Parse tag filters
    tag_filters = []
    for t in tags:
        if "=" in t:
            key, value = t.split("=", 1)
            tag_filters.append((key, value))
        else:
            tag_filters.append((t, None))  # Key only

    def matches_tags(item_tags: dict) -> bool:
        for key, value in tag_filters:
            if value is not None:
                if item_tags.get(key) != value:
                    return False
            else:
                if key not in item_tags:
                    return False
        return True

    # Check current version first
    current = kp.get_now()
    if current and matches_tags(current.tags):
        return current

    # Scan archived versions (newest first)
    versions = kp.list_versions(NOWDOC_ID, limit=100, collection=collection)
    for i, v in enumerate(versions):
        if matches_tags(v.tags):
            # Found match - get full item at this version offset
            return kp.get_version(NOWDOC_ID, i + 1, collection=collection)

    return None


@app.command()
def reflect():
    """Print the reflection practice guide."""
    # Installed package (copied by hatch force-include)
    reflect_path = Path(__file__).parent / "data" / "reflect.md"
    if not reflect_path.exists():
        # Development fallback: read from repo root
        reflect_path = Path(__file__).parent.parent / "commands" / "reflect.md"
    if reflect_path.exists():
        typer.echo(reflect_path.read_text())
    else:
        typer.echo("Reflection practice not found.", err=True)
        raise typer.Exit(1)


@app.command()
def get(
    id: Annotated[list[str], typer.Argument(help="URI(s) of item(s) (append @V{N} for version)")],
    version: Annotated[Optional[int], typer.Option(
        "--version", "-V",
        help="Get specific version (0=current, 1=previous, etc.)"
    )] = None,
    history: Annotated[bool, typer.Option(
        "--history", "-H",
        help="List all versions"
    )] = False,
    similar: Annotated[bool, typer.Option(
        "--similar", "-S",
        help="List similar items"
    )] = False,
    no_similar: Annotated[bool, typer.Option(
        "--no-similar",
        help="Suppress similar items in output"
    )] = False,
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Require tag (key or key=value, repeatable)"
    )] = None,
    limit: Annotated[int, typer.Option(
        "--limit", "-n",
        help="Max items for --history or --similar (default: 10)"
    )] = 10,
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Retrieve item(s) by ID.

    Accepts one or more IDs. Version identifiers: Append @V{N} to get a specific version.

    \b
    Examples:
        keep get doc:1                  # Current version with similar items
        keep get doc:1 doc:2 doc:3      # Multiple items
        keep get doc:1 -V 1             # Previous version with prev/next nav
        keep get "doc:1@V{1}"           # Same as -V 1
        keep get doc:1 --history        # List all versions
        keep get doc:1 --similar        # List similar items
        keep get doc:1 --no-similar     # Suppress similar items
        keep get doc:1 -t project=myapp # Only if tag matches
    """
    kp = _get_keeper(store, collection)
    outputs = []
    errors = []

    for one_id in id:
        result = _get_one(kp, one_id, version, history, similar, no_similar, tag, limit, collection)
        if result is None:
            errors.append(one_id)
        else:
            outputs.append(result)

    if outputs:
        separator = "\n" if _get_ids_output() else "\n---\n" if len(outputs) > 1 else ""
        typer.echo(separator.join(outputs))

    if errors:
        raise typer.Exit(1)


def _get_one(
    kp: Keeper,
    one_id: str,
    version: Optional[int],
    history: bool,
    similar: bool,
    no_similar: bool,
    tag: Optional[list[str]],
    limit: int,
    collection: str,
) -> Optional[str]:
    """Get a single item and return its formatted output, or None on error."""

    # Parse @V{N} version identifier from ID (security: check literal first)
    actual_id = one_id
    version_from_id = None

    if kp.exists(one_id, collection=collection):
        # Literal ID exists - use it directly (prevents confusion attacks)
        actual_id = one_id
    else:
        # Try parsing @V{N} suffix
        match = VERSION_SUFFIX_PATTERN.search(one_id)
        if match:
            version_from_id = int(match.group(1))
            actual_id = one_id[:match.start()]

    # Version from ID only applies if --version not explicitly provided
    effective_version = version
    if version is None and version_from_id is not None:
        effective_version = version_from_id

    if history:
        # List all versions
        versions = kp.list_versions(actual_id, limit=limit, collection=collection)
        current = kp.get(actual_id, collection=collection)

        if _get_ids_output():
            # Output version identifiers, one per line
            lines = []
            if current:
                lines.append(f"{actual_id}@V{{0}}")
            for i in range(1, len(versions) + 1):
                lines.append(f"{actual_id}@V{{{i}}}")
            return "\n".join(lines)
        elif _get_json_output():
            result = {
                "id": actual_id,
                "current": {
                    "summary": current.summary if current else None,
                    "tags": current.tags if current else {},
                    "offset": 0,
                    "vid": f"{actual_id}@V{{0}}",
                } if current else None,
                "versions": [
                    {
                        "offset": i + 1,
                        "vid": f"{actual_id}@V{{{i + 1}}}",
                        "version": v.version,
                        "summary": v.summary,
                        "created_at": v.created_at,
                    }
                    for i, v in enumerate(versions)
                ],
            }
            return json.dumps(result, indent=2)
        else:
            lines = []
            if current:
                summary_preview = current.summary[:60].replace("\n", " ")
                if len(current.summary) > 60:
                    summary_preview += "..."
                lines.append(f"v0 (current): {summary_preview}")
            if versions:
                lines.append(f"\nArchived:")
                for i, v in enumerate(versions, start=1):
                    date_part = v.created_at[:10] if v.created_at else "unknown"
                    summary_preview = v.summary[:50].replace("\n", " ")
                    if len(v.summary) > 50:
                        summary_preview += "..."
                    lines.append(f"  v{i} ({date_part}): {summary_preview}")
            else:
                lines.append("No version history.")
            return "\n".join(lines)

    if similar:
        # List similar items
        similar_items = kp.get_similar_for_display(actual_id, limit=limit, collection=collection)
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}

        if _get_ids_output():
            # Output version-scoped IDs one per line
            lines = []
            for item in similar_items:
                base_id = item.tags.get("_base_id", item.id)
                offset = similar_offsets.get(item.id, 0)
                lines.append(f"{base_id}@V{{{offset}}}")
            return "\n".join(lines)
        elif _get_json_output():
            result = {
                "id": actual_id,
                "similar": [
                    {
                        "id": f"{item.tags.get('_base_id', item.id)}@V{{{similar_offsets.get(item.id, 0)}}}",
                        "score": item.score,
                        "date": item.tags.get("_updated", item.tags.get("_created", ""))[:10],
                        "summary": item.summary[:60],
                    }
                    for item in similar_items
                ],
            }
            return json.dumps(result, indent=2)
        else:
            lines = [f"Similar to {actual_id}:"]
            if similar_items:
                for item in similar_items:
                    base_id = item.tags.get("_base_id", item.id)
                    offset = similar_offsets.get(item.id, 0)
                    score_str = f"({item.score:.2f})" if item.score else ""
                    date_part = item.tags.get("_updated", item.tags.get("_created", ""))[:10]
                    summary_preview = item.summary[:50].replace("\n", " ")
                    if len(item.summary) > 50:
                        summary_preview += "..."
                    lines.append(f"  {base_id}@V{{{offset}}} {score_str} {date_part} {summary_preview}")
            else:
                lines.append("  No similar items found.")
            return "\n".join(lines)

    # Get specific version or current
    offset = effective_version if effective_version is not None else 0

    if offset == 0:
        item = kp.get(actual_id, collection=collection)
        internal_version = None
    else:
        item = kp.get_version(actual_id, offset, collection=collection)
        # Calculate internal version number for API call
        versions = kp.list_versions(actual_id, limit=1, collection=collection)
        if versions:
            internal_version = versions[0].version - (offset - 1)
        else:
            internal_version = None

    if item is None:
        if offset > 0:
            typer.echo(f"Version not found: {actual_id} (offset {offset})", err=True)
        else:
            typer.echo(f"Not found: {actual_id}", err=True)
        return None

    # Check tag filter if specified
    if tag:
        filtered = _filter_by_tags([item], tag)
        if not filtered:
            typer.echo(f"Tag filter not matched: {actual_id}", err=True)
            return None

    # Get version navigation
    version_nav = kp.get_version_nav(actual_id, internal_version, collection=collection)

    # Get similar items and meta sections (unless suppressed or viewing old version)
    similar_items = None
    similar_offsets = None
    meta_sections = None
    if not no_similar and offset == 0:
        similar_items = kp.get_similar_for_display(actual_id, limit=3, collection=collection)
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(actual_id, collection=collection)

    return _format_item(
        item,
        as_json=_get_json_output(),
        version_nav=version_nav,
        viewing_offset=offset if offset > 0 else None,
        similar_items=similar_items,
        similar_offsets=similar_offsets,
        meta_sections=meta_sections,
    )


@app.command("del")
def del_cmd(
    id: Annotated[list[str], typer.Argument(help="ID(s) of item(s) to delete")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Delete the current version of item(s).

    If an item has version history, reverts to the previous version.
    If no history exists, removes the item completely.

    \b
    Examples:
        keep del %abc123def456        # Remove a text note
        keep del %abc123 %def456      # Remove multiple items
        keep del now                  # Revert now to previous
    """
    kp = _get_keeper(store, collection)
    had_errors = False

    for one_id in id:
        item = kp.get(one_id, collection=collection)
        if item is None:
            typer.echo(f"Not found: {one_id}", err=True)
            had_errors = True
            continue

        restored = kp.revert(one_id, collection=collection)

        if restored is None:
            # Fully deleted
            typer.echo(_format_summary_line(item))
        else:
            # Reverted — show the restored version with similar items
            similar_items = kp.get_similar_for_display(restored.id, limit=3, collection=collection)
            similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
            typer.echo(_format_item(
                restored,
                as_json=_get_json_output(),
                similar_items=similar_items if similar_items else None,
                similar_offsets=similar_offsets if similar_items else None,
            ))

    if had_errors:
        raise typer.Exit(1)


@app.command("delete", hidden=True)
def delete(
    id: Annotated[list[str], typer.Argument(help="ID(s) of item(s) to delete")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """Delete the current version of item(s) (alias for 'del')."""
    del_cmd(id=id, store=store, collection=collection)


@app.command("collections")
def list_collections(
    store: StoreOption = None,
):
    """
    List all collections in the store.
    """
    kp = _get_keeper(store, "default")
    collections = kp.list_collections()

    if _get_json_output():
        typer.echo(json.dumps(collections))
    else:
        if not collections:
            typer.echo("No collections.")
        else:
            for c in collections:
                typer.echo(c)




def _get_config_value(cfg, store_path: Path, path: str):
    """
    Get config value by dotted path.

    Special paths (not in TOML):
        file - config file location
        tool - package directory (SKILL.md location)
        openclaw-plugin - OpenClaw plugin directory
        store - store path
        collections - list of collections

    Dotted paths into config:
        providers - all provider config
        providers.embedding - embedding provider name
        providers.summarization - summarization provider name
        embedding.* - embedding config details
        summarization.* - summarization config details
        tags - default tags
    """
    # Special built-in paths (not in TOML)
    if path == "file":
        return str(cfg.config_path) if cfg else None
    if path == "tool":
        return str(get_tool_directory())
    if path == "openclaw-plugin":
        import importlib.resources
        return str(Path(str(importlib.resources.files("keep"))) / "data" / "openclaw-plugin")
    if path == "store":
        return str(store_path)
    if path == "collections":
        # Use ChromaStore directly to avoid full Keeper init
        from .store import ChromaStore
        try:
            chroma = ChromaStore(store_path)
            return chroma.list_collections()
        except Exception:
            return []

    # Provider shortcuts
    if path == "providers":
        if cfg:
            return {
                "embedding": cfg.embedding.name,
                "summarization": cfg.summarization.name,
                "document": cfg.document.name,
            }
        return None
    if path == "providers.embedding":
        return cfg.embedding.name if cfg else None
    if path == "providers.summarization":
        return cfg.summarization.name if cfg else None
    if path == "providers.document":
        return cfg.document.name if cfg else None

    # Tags shortcut
    if path == "tags":
        return cfg.default_tags if cfg else {}

    # Dotted path into config attributes
    if not cfg:
        raise typer.BadParameter(f"No config loaded, cannot access: {path}")

    parts = path.split(".")
    value = cfg
    for part in parts:
        if hasattr(value, part):
            value = getattr(value, part)
        elif hasattr(value, "params") and part in value.params:
            # Provider config params
            value = value.params[part]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise typer.BadParameter(f"Unknown config path: {path}")

    # Return name for provider objects
    if hasattr(value, "name") and hasattr(value, "params"):
        return value.name
    return value


def _format_config_with_defaults(cfg, store_path: Path) -> str:
    """Format config output with commented defaults for unused settings."""
    config_path = cfg.config_path if cfg else None
    lines = []

    # Get collections using ChromaStore directly (no API calls)
    from .store import ChromaStore
    try:
        chroma = ChromaStore(store_path)
        collections = chroma.list_collections()
    except Exception:
        collections = []

    # Show paths
    lines.append(f"file: {config_path}")
    lines.append(f"tool: {get_tool_directory()}")
    lines.append(f"store: {store_path}")
    lines.append(f"collections: {collections}")

    if cfg:
        lines.append("")
        lines.append("providers:")
        lines.append(f"  embedding: {cfg.embedding.name if cfg.embedding else 'none'}")
        if cfg.embedding and cfg.embedding.params.get("model"):
            lines.append(f"    model: {cfg.embedding.params['model']}")
        lines.append(f"  summarization: {cfg.summarization.name if cfg.summarization else 'none'}")
        if cfg.summarization and cfg.summarization.params.get("model"):
            lines.append(f"    model: {cfg.summarization.params['model']}")

        # Show configured tags or example
        if cfg.default_tags:
            lines.append("")
            lines.append("tags:")
            for key, value in cfg.default_tags.items():
                lines.append(f"  {key}: {value}")
        else:
            lines.append("")
            lines.append("# tags:")
            lines.append("#   project: myproject")

        # Show integrations status
        from .integrations import TOOL_CONFIGS
        if cfg.integrations:
            lines.append("")
            lines.append("integrations:")
            for tool_key in TOOL_CONFIGS:
                if tool_key in cfg.integrations:
                    status = cfg.integrations[tool_key]
                    lines.append(f"  {tool_key}: {status}")
            for tool_key in TOOL_CONFIGS:
                if tool_key not in cfg.integrations:
                    lines.append(f"  # {tool_key}: false")
        else:
            lines.append("")
            lines.append("# integrations:")
            for tool_key in TOOL_CONFIGS:
                lines.append(f"#   {tool_key}: false")

        # Show available options as comments
        lines.append("")
        lines.append("# --- Configuration Options ---")
        lines.append("#")
        lines.append("# API Keys (set in environment):")
        lines.append("#   VOYAGE_API_KEY     → embedding: voyage (Anthropic's partner)")
        lines.append("#   ANTHROPIC_API_KEY  → summarization: anthropic")
        lines.append("#   OPENAI_API_KEY     → embedding: openai, summarization: openai")
        lines.append("#   GEMINI_API_KEY     → embedding: gemini, summarization: gemini")
        lines.append("#")
        lines.append("# Models (configure in keep.toml):")
        lines.append("#   voyage: voyage-3.5-lite (default), voyage-3-large, voyage-code-3")
        lines.append("#   anthropic: claude-3-haiku-20240307 (default), claude-3-5-haiku-20241022")
        lines.append("#   openai embedding: text-embedding-3-small (default), text-embedding-3-large")
        lines.append("#   openai summarization: gpt-4o-mini (default)")
        lines.append("#   gemini embedding: text-embedding-004 (default)")
        lines.append("#   gemini summarization: gemini-3-flash-preview (default)")
        lines.append("#")
        lines.append("# Ollama (auto-detected if running, no API key needed):")
        lines.append("#   OLLAMA_HOST        → default: http://localhost:11434")
        lines.append("#   ollama embedding: any model (prefer nomic-embed-text, mxbai-embed-large)")
        lines.append("#   ollama summarization: any generative model (e.g. llama3.2, mistral)")

    return "\n".join(lines)


@app.command()
def config(
    path: Annotated[Optional[str], typer.Argument(
        help="Config path to get (e.g., 'file', 'tool', 'store', 'providers.embedding')"
    )] = None,
    reset_system_docs: Annotated[bool, typer.Option(
        "--reset-system-docs",
        help="Force reload system documents from bundled content (overwrites modifications)"
    )] = False,
    store: StoreOption = None,
):
    """
    Show configuration. Optionally get a specific value by path.

    \b
    Examples:
        keep config              # Show all config
        keep config file         # Config file location
        keep config tool         # Package directory (SKILL.md location)
        keep config openclaw-plugin  # OpenClaw plugin directory
        keep config store        # Store path
        keep config providers    # All provider config
        keep config providers.embedding  # Embedding provider name
        keep config --reset-system-docs  # Reset bundled system docs
    """
    # Handle system docs reset - requires full Keeper initialization
    if reset_system_docs:
        kp = _get_keeper(store, "default")
        stats = kp.reset_system_documents()
        typer.echo(f"Reset {stats['reset']} system documents")
        return

    # For config display, use lightweight path (no API calls)
    from .config import load_or_create_config
    from .paths import get_config_dir, get_default_store_path

    actual_store = store if store is not None else _get_store_override()
    if actual_store is not None:
        config_dir = Path(actual_store).resolve()
    else:
        config_dir = get_config_dir()

    cfg = load_or_create_config(config_dir)
    config_path = cfg.config_path if cfg else None
    store_path = get_default_store_path(cfg) if actual_store is None else actual_store

    # If a specific path is requested, return just that value
    if path:
        try:
            value = _get_config_value(cfg, store_path, path)
        except typer.BadParameter as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

        if _get_json_output():
            typer.echo(json.dumps({path: value}, indent=2))
        else:
            # Raw output for shell scripting
            if isinstance(value, (list, dict)):
                typer.echo(json.dumps(value))
            else:
                typer.echo(value)
        return

    # Full config output
    if _get_json_output():
        # Get collections using ChromaStore directly (no API calls)
        from .store import ChromaStore
        try:
            chroma = ChromaStore(store_path)
            collections = chroma.list_collections()
        except Exception:
            collections = []

        result = {
            "file": str(config_path) if config_path else None,
            "tool": str(get_tool_directory()),
            "store": str(store_path),
            "collections": collections,
            "providers": {
                "embedding": cfg.embedding.name if cfg else None,
                "summarization": cfg.summarization.name if cfg else None,
                "document": cfg.document.name if cfg else None,
            },
        }
        if cfg and cfg.default_tags:
            result["tags"] = cfg.default_tags
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(_format_config_with_defaults(cfg, store_path))


@app.command("process-pending")
def process_pending(
    store: StoreOption = None,
    limit: Annotated[int, typer.Option(
        "--limit", "-n",
        help="Maximum items to process in this batch"
    )] = 10,
    all_items: Annotated[bool, typer.Option(
        "--all", "-a",
        help="Process all pending items (ignores --limit)"
    )] = False,
    daemon: Annotated[bool, typer.Option(
        "--daemon",
        hidden=True,
        help="Run as background daemon (used internally)"
    )] = False,
):
    """
    Process pending summaries from lazy indexing.

    Items indexed with --lazy use a truncated placeholder summary.
    This command generates real summaries for those items.
    """
    kp = _get_keeper(store, "default")

    # Daemon mode: acquire singleton lock, process all, clean up
    if daemon:
        import signal
        from .model_lock import ModelLock

        pid_path = kp._processor_pid_path
        processor_lock = ModelLock(kp._store_path / ".processor.lock")
        shutdown_requested = False

        # Acquire exclusive lock (non-blocking) — ensures true singleton
        if not processor_lock.acquire(blocking=False):
            # Another daemon is already running
            kp.close()
            return

        def handle_signal(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True

        # Handle common termination signals gracefully
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            # Write PID file (informational, lock is authoritative)
            pid_path.write_text(str(os.getpid()))

            # Process all items until queue empty or shutdown requested
            while not shutdown_requested:
                result = kp.process_pending(limit=50)
                if result["processed"] == 0 and result["failed"] == 0:
                    break

        finally:
            # Clean up PID file
            try:
                pid_path.unlink()
            except OSError:
                pass
            # Close resources (releases model locks via provider release())
            kp.close()
            # Release processor singleton lock
            processor_lock.release()
        return

    # Interactive mode
    pending_before = kp.pending_count()

    if pending_before == 0:
        if _get_json_output():
            typer.echo(json.dumps({"processed": 0, "remaining": 0}))
        else:
            typer.echo("No pending summaries.")
        return

    if all_items:
        # Process all items in batches
        totals = {"processed": 0, "failed": 0, "abandoned": 0, "errors": []}
        while True:
            result = kp.process_pending(limit=50)
            totals["processed"] += result["processed"]
            totals["failed"] += result["failed"]
            totals["abandoned"] += result["abandoned"]
            totals["errors"].extend(result["errors"])
            if result["processed"] == 0 and result["failed"] == 0:
                break
            if not _get_json_output():
                typer.echo(f"  Processed {totals['processed']}...")

        remaining = kp.pending_count()
        if _get_json_output():
            typer.echo(json.dumps({
                "processed": totals["processed"],
                "failed": totals["failed"],
                "abandoned": totals["abandoned"],
                "remaining": remaining,
                "errors": totals["errors"][:10],  # Limit error output
            }))
        else:
            msg = f"✓ Processed {totals['processed']} items"
            if totals["failed"]:
                msg += f", {totals['failed']} failed"
            if totals["abandoned"]:
                msg += f", {totals['abandoned']} abandoned"
            msg += f", {remaining} remaining"
            typer.echo(msg)
            # Show first few errors
            for err in totals["errors"][:3]:
                typer.echo(f"  Error: {err}", err=True)
    else:
        # Process limited batch
        result = kp.process_pending(limit=limit)
        remaining = kp.pending_count()

        if _get_json_output():
            typer.echo(json.dumps({
                "processed": result["processed"],
                "failed": result["failed"],
                "abandoned": result["abandoned"],
                "remaining": remaining,
                "errors": result["errors"][:10],
            }))
        else:
            msg = f"✓ Processed {result['processed']} items"
            if result["failed"]:
                msg += f", {result['failed']} failed"
            if result["abandoned"]:
                msg += f", {result['abandoned']} abandoned"
            msg += f", {remaining} remaining"
            typer.echo(msg)
            # Show first few errors
            for err in result["errors"][:3]:
                typer.echo(f"  Error: {err}", err=True)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main():
    try:
        app()
    except SystemExit:
        raise  # Let typer handle exit codes
    except KeyboardInterrupt:
        raise SystemExit(130)  # Standard exit code for Ctrl+C
    except Exception as e:
        # Log full traceback to file, show clean message to user
        from .errors import log_exception, ERROR_LOG_PATH
        log_exception(e, context="keep CLI")
        typer.echo(f"Error: {e}", err=True)
        typer.echo(f"Details logged to {ERROR_LOG_PATH}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
