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
import select
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

# Pattern for version identifier suffix: @V{N} where N is digits only
VERSION_SUFFIX_PATTERN = re.compile(r'@V\{(\d+)\}$')

# Pattern for part identifier suffix: @P{N} where N is digits only
PART_SUFFIX_PATTERN = re.compile(r'@P\{(\d+)\}$')

# URI scheme pattern per RFC 3986: scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
# Used to distinguish URIs from plain text in the update command
_URI_SCHEME_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://')

from .api import Keeper, _text_content_id
from .config import get_tool_directory
from .document_store import VersionInfo
from .types import Item, local_date
from .logging_config import configure_quiet_mode, enable_debug_mode

# Maximum number of files to index from a directory at once
MAX_DIR_FILES = 1000


def _is_filesystem_path(source: str) -> Optional[Path]:
    """Return resolved Path if source is an existing filesystem path, None otherwise.

    Skips anything that looks like a URI (has ://). Uses expanduser + resolve.
    Conservative: only matches if the path actually exists on disk.
    """
    if _URI_SCHEME_PATTERN.match(source):
        return None
    try:
        resolved = Path(source).expanduser().resolve()
        if resolved.exists():
            return resolved
    except (OSError, ValueError):
        pass
    return None


def _list_directory_files(directory: Path) -> list[Path]:
    """List regular files in a directory, sorted by name.

    Skips symlinks, subdirectories, and hidden files (names starting with '.').
    """
    files = []
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir():
            continue
        files.append(entry)
    return files


def _output_width() -> int:
    """Terminal width for summary truncation. Use generous default when not a TTY."""
    if not sys.stdout.isatty():
        return 200
    return shutil.get_terminal_size((120, 24)).columns


def _has_stdin_data() -> bool:
    """Check if stdin has data available without blocking.

    Returns True only when stdin is a pipe with data ready to read.
    Returns False for TTYs, sockets (exec sandbox), and empty pipes.
    This prevents hanging when stdin is a socket that never sends EOF.
    """
    if sys.stdin.isatty():
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(ready)
    except (ValueError, OSError):
        return False


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
    parts_manifest: Optional[list] = None,
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
        parts_manifest: Optional list of PartInfo from structural decomposition

    Note: Offset computation (v1, v2, etc.) assumes version_nav lists
    are ordered newest-first, matching list_versions() ordering.
    Changing that ordering would break the vN = -V N correspondence.
    """
    cols = _output_width()

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
            date_part = local_date(sim_item.tags.get("_updated") or sim_item.tags.get("_created", ""))
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

    # Add parts manifest (structural decomposition)
    if parts_manifest:
        part_ids = [f"@P{{{p.part_num}}}" for p in parts_manifest]
        id_width = max(len(s) for s in part_ids)
        lines.append("parts:")
        for part, pid in zip(parts_manifest, part_ids):
            prefix_len = 4 + id_width + 1
            summary_preview = _truncate_summary(part.summary, prefix_len)
            lines.append(f"  - {pid.ljust(id_width)} {summary_preview}")

    # Add version navigation (just @V{N} since ID is shown at top, with date + summary)
    if version_nav:
        # Current offset (0 if viewing current)
        current_offset = viewing_offset if viewing_offset is not None else 0

        if version_nav.get("prev"):
            prev_ids = [f"@V{{{current_offset + i + 1}}}" for i in range(len(version_nav["prev"]))]
            id_width = max(len(s) for s in prev_ids)
            lines.append("prev:")
            for vid, v in zip(prev_ids, version_nav["prev"]):
                date_part = local_date(v.created_at) if v.created_at else ""
                prefix_len = 4 + id_width + 1 + len(date_part) + 1
                summary_preview = _truncate_summary(v.summary, prefix_len)
                lines.append(f"  - {vid.ljust(id_width)} {date_part} {summary_preview}")
        if version_nav.get("next"):
            next_ids = [f"@V{{{current_offset - i - 1}}}" for i in range(len(version_nav["next"]))]
            id_width = max(len(s) for s in next_ids)
            lines.append("next:")
            for vid, v in zip(next_ids, version_nav["next"]):
                date_part = local_date(v.created_at) if v.created_at else ""
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
    # Get version/part-scoped ID
    base_id = item.tags.get("_base_id", item.id)
    part_num = item.tags.get("_part_num")
    version = item.tags.get("_version", "0")
    if part_num:
        suffix = f"@P{{{part_num}}}"
    elif version != "0":
        suffix = f"@V{{{version}}}"
    else:
        suffix = ""
    versioned_id = f"{_shell_quote_id(base_id)}{suffix}"

    # Pad ID for column alignment
    padded_id = versioned_id.ljust(id_width) if id_width else versioned_id

    # Get date in local timezone
    date = local_date(item.tags.get("_updated") or item.tags.get("_created", ""))

    # Truncate summary to fit terminal width, collapse newlines
    cols = _output_width()
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
        help="Output full notes (overrides --ids)",
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
        kp = _get_keeper(None)
        item = kp.get_now()
        version_nav = kp.get_version_nav(NOWDOC_ID, None)
        similar_items = kp.get_similar_for_display(NOWDOC_ID, limit=3)  # bare `keep`: default 3
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(NOWDOC_ID, limit_per_doc=3)
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
        help="Only notes updated since (ISO duration: P3D, P1W, PT1H; or date: 2026-01-15)"
    )
]

UntilOption = Annotated[
    Optional[str],
    typer.Option(
        "--until",
        help="Only notes updated before (ISO duration: P3D, P1W, PT1H; or date: 2026-01-15)"
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
    parts_manifest: Optional[list] = None,
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
        parts_manifest: List of PartInfo for structural decomposition
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
                    "date": local_date(s.tags.get("_updated") or s.tags.get("_created", "")),
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
        if parts_manifest:
            result["parts"] = [
                {
                    "part": p.part_num,
                    "pid": f"{item.id}@P{{{p.part_num}}}",
                    "summary": p.summary[:60],
                }
                for p in parts_manifest
            ]
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
    if _get_full_output() or version_nav or similar_items or viewing_offset is not None or meta_sections or parts_manifest:
        return _format_yaml_frontmatter(
            item, version_nav, viewing_offset,
            similar_items, similar_offsets, meta_sections,
            parts_manifest=parts_manifest,
        )
    return _format_summary_line(item)


def _versions_to_items(doc_id: str, current: Item | None, versions: list) -> list[Item]:
    """Convert current item + previous VersionInfo list into Items for _format_items."""
    items: list[Item] = []
    if current:
        items.append(current)
    for i, v in enumerate(versions, start=1):
        tags = dict(v.tags)
        tags["_version"] = str(i)
        tags["_updated"] = v.created_at or ""
        tags["_updated_date"] = (v.created_at or "")[:10]
        items.append(Item(id=doc_id, summary=v.summary, tags=tags))
    return items


def _parts_to_items(doc_id: str, current: Item | None, parts: list) -> list[Item]:
    """Convert current item + PartInfo list into Items for _format_items."""
    items: list[Item] = []
    if current:
        items.append(current)
    for p in parts:
        tags = dict(p.tags)
        tags["_part_num"] = str(p.part_num)
        tags["_base_id"] = doc_id
        tags["_updated"] = p.created_at or ""
        items.append(Item(id=doc_id, summary=p.summary, tags=tags))
    return items


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

  Hosted (simplest — no local setup):
    export KEEPNOTES_API_KEY=...   # Sign up at https://keepnotes.ai

  API-based:
    export VOYAGE_API_KEY=...      # Get at dash.voyageai.com
    export ANTHROPIC_API_KEY=...   # Optional: for better summaries

  Local (macOS Apple Silicon):
    pip install 'keep-skill[local]'

See: https://github.com/hughpyle/keep#installation
"""


def _get_keeper(store: Optional[Path]) -> Keeper:
    """Initialize memory, handling errors gracefully.

    Returns a local Keeper or RemoteKeeper depending on config.
    Both satisfy the same protocol — the CLI doesn't distinguish.
    """
    import atexit

    # Check for remote backend config (env vars or TOML [remote] section)
    api_url = os.environ.get("KEEPNOTES_API_URL", "https://api.keepnotes.ai")
    api_key = os.environ.get("KEEPNOTES_API_KEY")
    if api_url and api_key:
        from .config import get_config_dir, load_or_create_config
        from .remote import RemoteKeeper
        try:
            config_dir = get_config_dir()
            config = load_or_create_config(config_dir)
            kp = RemoteKeeper(api_url, api_key, config)
            atexit.register(kp.close)
            return kp
        except Exception as e:
            typer.echo(f"Error connecting to remote: {e}", err=True)
            raise typer.Exit(1)

    # Check global override from --store on main command
    actual_store = store if store is not None else _get_store_override()
    try:
        kp = Keeper(actual_store)
        # Ensure close() runs before interpreter shutdown to release model locks
        atexit.register(kp.close)

        # Check for remote config in TOML (loaded during Keeper init)
        if kp.config and kp.config.remote:
            from .remote import RemoteKeeper
            remote = RemoteKeeper(
                kp.config.remote.api_url,
                kp.config.remote.api_key,
                kp.config,
            )
            atexit.register(remote.close)
            kp.close()  # Don't need the local Keeper
            return remote

        # Warn (don't exit) if no embedding provider — read-only ops still work
        if kp.config and kp.config.embedding is None:
            typer.echo(NO_PROVIDER_ERROR.strip(), err=True)
        # Check tool integrations (fast path: dict lookup, no I/O)
        if kp.config:
            from .integrations import check_and_install
            try:
                check_and_install(kp.config)
            except (OSError, ValueError) as e:
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
        parsed[k.casefold()] = v
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
            key = key.casefold()
            # Case-insensitive value comparison
            result = [item for item in result
                      if (item.tags.get(key) or "").casefold() == value.casefold()]
        else:
            # Key only - check if key exists
            result = [item for item in result if t.casefold() in item.tags]
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
        help="Find notes similar to this ID (instead of text search)"
    )] = None,
    include_self: Annotated[bool, typer.Option(
        help="Include the queried note (only with --id)"
    )] = False,
    text: Annotated[bool, typer.Option(
        "--text",
        help="Use full-text search instead of semantic similarity"
    )] = False,
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Filter by tag (key or key=value, repeatable)"
    )] = None,
    store: StoreOption = None,
    limit: LimitOption = 10,
    since: SinceOption = None,
    until: UntilOption = None,
    history: Annotated[bool, typer.Option(
        "--history", "-H",
        help="Include versions of matching notes"
    )] = False,
    show_all: Annotated[bool, typer.Option(
        "--all", "-a",
        help="Include hidden system notes (IDs starting with '.')"
    )] = False,
):
    """
    Find notes by semantic similarity (default) or full-text search.

    \b
    Examples:
        keep find "authentication"              # Semantic search
        keep find "auth" --text                 # Full-text search
        keep find --id file:///path/to/doc.md   # Find similar notes
        keep find "auth" -t project=myapp       # Search + filter by tag
        keep find "auth" --history              # Include versions
    """
    if id and query:
        typer.echo("Error: Specify either a query or --id, not both", err=True)
        raise typer.Exit(1)
    if not id and not query:
        typer.echo("Error: Specify a query or --id", err=True)
        raise typer.Exit(1)
    if id and text:
        typer.echo("Error: --text cannot be used with --id", err=True)
        raise typer.Exit(1)

    kp = _get_keeper(store)

    # Search with higher limit if filtering, then post-filter
    search_limit = limit * 5 if tag else limit

    if id:
        results = kp.find(similar_to=id, limit=search_limit, since=since, until=until, include_self=include_self, include_hidden=show_all)
    else:
        results = kp.find(query, fulltext=text, limit=search_limit, since=since, until=until, include_hidden=show_all)

    # Post-filter by tags if specified
    if tag:
        results = _filter_by_tags(results, tag)

    results = results[:limit]

    # Expand with versions if requested
    if history:
        expanded: list[Item] = []
        for item in results:
            versions = kp.list_versions(item.id, limit=limit)
            expanded.extend(_versions_to_items(item.id, item, versions))
        results = expanded

    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command(hidden=True)
def search(
    query: Annotated[str, typer.Argument(default=..., help="Full-text search query")],
    store: StoreOption = None,
    limit: LimitOption = 10,
    since: SinceOption = None,
    until: UntilOption = None,
):
    """
    Search note summaries using full-text search (alias for find --text).
    """
    kp = _get_keeper(store)
    results = kp.find(query, fulltext=True, limit=limit, since=since, until=until)
    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command("list")
def list_recent(
    store: StoreOption = None,
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
    until: UntilOption = None,
    history: Annotated[bool, typer.Option(
        "--history", "-H",
        help="Include versions in output"
    )] = False,
    parts: Annotated[bool, typer.Option(
        "--parts", "-P",
        help="Include structural parts (from analyze)"
    )] = False,
    show_all: Annotated[bool, typer.Option(
        "--all", "-a",
        help="Include hidden system notes (IDs starting with '.')"
    )] = False,
):
    """
    List recent notes, filter by tags, or list tag keys/values.

    \b
    Examples:
        keep list                      # Recent notes (by update time)
        keep list --sort accessed      # Recent notes (by access time)
        keep list --tag foo            # Notes with tag 'foo' (any value)
        keep list --tag foo=bar        # Notes with tag foo=bar
        keep list --tag foo --tag bar  # Notes with both tags
        keep list --tags=              # List all tag keys
        keep list --tags=foo           # List values for tag 'foo'
        keep list --since P3D          # Notes updated in last 3 days
        keep list --until 2026-01-15   # Notes updated before date
        keep list --history            # Include versions
        keep list --parts              # Include analyzed parts
    """
    kp = _get_keeper(store)

    # --tags mode: list keys or values
    if tags is not None:
        # Empty string means list all keys, otherwise list values for key
        key = tags if tags else None
        values = kp.list_tags(key)
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
                matches = kp.query_tag(key, value, limit=limit, since=since, until=until, include_hidden=show_all)
            else:
                # Key only - find items with this tag key (any value)
                matches = kp.query_tag(t, limit=limit, since=since, until=until, include_hidden=show_all)

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
    results = kp.list_recent(limit=limit, since=since, until=until, order_by=sort, include_history=history, include_hidden=show_all)

    # Expand with parts if requested
    if parts:
        expanded: list[Item] = []
        for item in results:
            part_list = kp.list_parts(item.id)
            if part_list:
                expanded.extend(_parts_to_items(item.id, item, part_list))
            else:
                expanded.append(item)
        results = expanded

    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command("tag-update")
def tag_update(
    ids: Annotated[list[str], typer.Argument(default=..., help="Note IDs to tag")],
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Tag as key=value (empty value removes: key=)"
    )] = None,
    remove: Annotated[Optional[list[str]], typer.Option(
        "--remove", "-r",
        help="Tag keys to remove"
    )] = None,
    store: StoreOption = None,
):
    """
    Add, update, or remove tags on existing notes.

    Does not re-process the note - only updates tags.

    \b
    Examples:
        keep tag-update doc:1 --tag project=myapp
        keep tag-update doc:1 doc:2 --tag status=reviewed
        keep tag-update doc:1 --remove obsolete_tag
        keep tag-update doc:1 --tag temp=  # Remove via empty value
    """
    kp = _get_keeper(store)

    # Parse tags from key=value format
    tag_changes = _parse_tags(tags)

    # Add explicit removals as empty strings
    if remove:
        for key in remove:
            tag_changes[key] = ""

    if not tag_changes:
        typer.echo("Error: Specify at least one --tag or --remove", err=True)
        raise typer.Exit(1)

    # Process each document (route parts to tag_part)
    results = []
    for doc_id in ids:
        match = PART_SUFFIX_PATTERN.search(doc_id)
        try:
            if match:
                part_num = int(match.group(1))
                base_id = doc_id[:match.start()]
                part = kp.tag_part(base_id, part_num, tags=tag_changes)
                if part is None:
                    typer.echo(f"Part not found: {doc_id}", err=True)
                else:
                    typer.echo(f"Updated {doc_id}")
            else:
                item = kp.tag(doc_id, tags=tag_changes)
                if item is None:
                    typer.echo(f"Not found: {doc_id}", err=True)
                else:
                    results.append(item)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    if results:
        typer.echo(_format_items(results, as_json=_get_json_output()))


def _put_store(
    kp: "Keeper",
    source: Optional[str],
    resolved_path: Optional[Path],
    parsed_tags: dict,
    id: Optional[str],
    summary: Optional[str],
    do_analyze: bool,
) -> Optional["Item"]:
    """Execute the store operation for put(). Returns Item, or None for directory mode."""
    if source == "-" or (source is None and _has_stdin_data()):
        # Stdin mode: explicit '-' or piped input
        try:
            content = sys.stdin.read()
        except UnicodeDecodeError:
            typer.echo("Error: stdin contains binary data (not valid UTF-8)", err=True)
            typer.echo("Hint: for binary files, use: keep put file:///path/to/file", err=True)
            raise typer.Exit(1)
        content, frontmatter_tags = _parse_frontmatter(content)
        parsed_tags = {**frontmatter_tags, **parsed_tags}  # CLI tags override
        if summary is not None:
            typer.echo("Error: --summary cannot be used with stdin input (original content would be lost)", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file --summary '...'", err=True)
            raise typer.Exit(1)
        max_len = kp.config.max_summary_length
        if len(content) > max_len:
            typer.echo(f"Error: stdin content too long to store inline ({len(content)} chars, max {max_len})", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file", err=True)
            raise typer.Exit(1)
        # Use content-addressed ID for stdin text (enables versioning)
        doc_id = id or _text_content_id(content)
        return kp.put(content, id=doc_id, tags=parsed_tags or None)
    elif resolved_path is not None and resolved_path.is_dir():
        # Directory mode: index all regular files in directory
        if summary is not None:
            typer.echo("Error: --summary cannot be used with directory mode", err=True)
            raise typer.Exit(1)
        if id is not None:
            typer.echo("Error: --id cannot be used with directory mode", err=True)
            raise typer.Exit(1)
        files = _list_directory_files(resolved_path)
        if not files:
            typer.echo(f"Error: no eligible files in {resolved_path}/", err=True)
            typer.echo("Hint: hidden files, symlinks, and subdirectories are skipped", err=True)
            raise typer.Exit(1)
        if len(files) > MAX_DIR_FILES:
            typer.echo(f"Error: directory has {len(files)} files (max {MAX_DIR_FILES})", err=True)
            typer.echo("Hint: use a smaller directory or index files individually", err=True)
            raise typer.Exit(1)
        results: list[Item] = []
        errors: list[str] = []
        total = len(files)
        for i, fpath in enumerate(files, 1):
            file_uri = f"file://{fpath}"
            try:
                item = kp.put(uri=file_uri, tags=parsed_tags or None)
                results.append(item)
                typer.echo(f"[{i}/{total}] {fpath.name} ok", err=True)
            except Exception as e:
                errors.append(f"{fpath.name}: {e}")
                typer.echo(f"[{i}/{total}] {fpath.name} error: {e}", err=True)
        indexed = len(results)
        skipped = len(errors)
        typer.echo(f"\n{indexed} indexed, {skipped} skipped from {resolved_path.name}/", err=True)
        if results:
            typer.echo(_format_items(results, as_json=_get_json_output()))
        if do_analyze and results:
            queued = 0
            for r in results:
                try:
                    if kp.enqueue_analyze(r.id):
                        queued += 1
                except ValueError:
                    pass
            if queued:
                typer.echo(f"Queued {queued} items for analysis.", err=True)
            else:
                typer.echo("All items already analyzed, nothing to do.", err=True)
        return None
    elif resolved_path is not None and resolved_path.is_file():
        # File mode: bare file path → normalize to file:// URI
        file_uri = f"file://{resolved_path}"
        return kp.put(uri=file_uri, tags=parsed_tags or None, summary=summary)
    elif source and _URI_SCHEME_PATTERN.match(source):
        # URI mode: fetch from URI (ID is the URI itself)
        return kp.put(uri=source, tags=parsed_tags or None, summary=summary)
    elif source:
        # Text mode: inline content (no :// in source)
        if summary is not None:
            typer.echo("Error: --summary cannot be used with inline text (original content would be lost)", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file --summary '...'", err=True)
            raise typer.Exit(1)
        max_len = kp.config.max_summary_length
        if len(source) > max_len:
            typer.echo(f"Error: inline text too long to store ({len(source)} chars, max {max_len})", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file", err=True)
            raise typer.Exit(1)
        # Use content-addressed ID for text (enables versioning)
        doc_id = id or _text_content_id(source)
        return kp.put(source, id=doc_id, tags=parsed_tags or None)
    else:
        typer.echo("Error: Provide content, URI, or '-' for stdin", err=True)
        raise typer.Exit(1)


@app.command("put")
def put(
    source: Annotated[Optional[str], typer.Argument(
        help="URI to fetch, text content, or '-' for stdin"
    )] = None,
    id: Annotated[Optional[str], typer.Option(
        "--id", "-i",
        help="Note ID (auto-generated for text/stdin modes)"
    )] = None,
    store: StoreOption = None,
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
        help="Show tag suggestions from similar notes"
    )] = False,
    do_analyze: Annotated[bool, typer.Option(
        "--analyze",
        help="Queue background analysis (decompose into parts)"
    )] = False,
):
    """
    Add or update a note in the store.

    \b
    Input modes (auto-detected):
      keep put /path/to/folder/   # Directory mode: index all files
      keep put /path/to/file.pdf  # File mode: index single file
      keep put file:///path       # URI mode: has ://
      keep put "my note"          # Text mode: content-addressed ID
      keep put -                  # Stdin mode: explicit -
      echo "pipe" | keep put      # Stdin mode: piped input

    \b
    Directory mode indexes all regular files (non-recursive).
    Skips hidden files, symlinks, and subdirectories.

    \b
    Text mode uses content-addressed IDs for versioning:
      keep put "my note"           # Creates %{hash}
      keep put "my note" -t done   # Same ID, new version (tag change)
      keep put "different note"    # Different ID (new doc)
    """
    kp = _get_keeper(store)
    parsed_tags = _parse_tags(tags)

    # Determine mode based on source content
    # Check for filesystem path (directory or file) before other modes
    resolved_path = _is_filesystem_path(source) if source and source != "-" else None

    try:
        item = _put_store(kp, source, resolved_path, parsed_tags, id, summary, do_analyze)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    if item is None:
        return  # directory mode already printed output

    # Surface similar items (occasion for reflection)
    suggest_limit = 10 if suggest_tags else 3
    similar_items = kp.get_similar_for_display(item.id, limit=suggest_limit)
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

    if do_analyze:
        try:
            if kp.enqueue_analyze(item.id):
                typer.echo(f"Queued {item.id} for background analysis.", err=True)
            else:
                typer.echo(f"Already analyzed, skipping {item.id}.", err=True)
        except ValueError:
            pass


@app.command("update", hidden=True)
def update(
    source: Annotated[Optional[str], typer.Argument(help="URI to fetch, text content, or '-' for stdin")] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    store: StoreOption = None,
    tags: Annotated[Optional[list[str]], typer.Option("--tag", "-t")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
):
    """Add or update a note (alias for 'put')."""
    put(source=source, id=id, store=store, tags=tags, summary=summary)


@app.command("add", hidden=True)
def add(
    source: Annotated[Optional[str], typer.Argument(help="URI to fetch, text content, or '-' for stdin")] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    store: StoreOption = None,
    tags: Annotated[Optional[list[str]], typer.Option("--tag", "-t")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
):
    """Add a note (alias for 'put')."""
    put(source=source, id=id, store=store, tags=tags, summary=summary)


@app.command()
def now(
    content: Annotated[Optional[str], typer.Argument(
        help="Content to set (omit to show current)"
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
    scope: Annotated[Optional[str], typer.Option(
        "--scope",
        help="Scope for multi-user isolation (e.g. user ID)"
    )] = None,
    store: StoreOption = None,
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Set tag (with content) or filter (without content)"
    )] = None,
    limit: Annotated[int, typer.Option(
        "--limit", "-n",
        help="Max similar/meta notes to show (default 3)"
    )] = 3,
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
        keep now -n 10                   # Show with more similar/meta items
        keep now --reset                 # Reset to default from system
        keep now -V 1                    # Previous version
        keep now --history               # List all versions
    """
    from .api import NOWDOC_ID

    kp = _get_keeper(store)
    doc_id = f"now:{scope}" if scope else NOWDOC_ID

    # Handle history listing
    if history:
        versions = kp.list_versions(doc_id, limit=limit)
        current = kp.get(doc_id)
        items = _versions_to_items(doc_id, current, versions)
        typer.echo(_format_items(items, as_json=_get_json_output()))
        return

    # Handle version retrieval
    if version is not None:
        offset = version
        if offset == 0:
            item = kp.get_now(scope=scope)
            internal_version = None
        else:
            item = kp.get_version(doc_id, offset)
            # Get internal version number for API call
            versions = kp.list_versions(doc_id, limit=1)
            if versions:
                internal_version = versions[0].version - (offset - 1)
            else:
                internal_version = None

        if item is None:
            typer.echo(f"Version not found (offset {offset})", err=True)
            raise typer.Exit(1)

        version_nav = kp.get_version_nav(doc_id, internal_version)
        typer.echo(_format_item(
            item,
            as_json=_get_json_output(),
            version_nav=version_nav,
            viewing_offset=offset if offset > 0 else None,
        ))
        return

    # Read from stdin if piped and no content argument
    if content is None and not reset and _has_stdin_data():
        try:
            content = sys.stdin.read().strip() or None
        except UnicodeDecodeError:
            typer.echo("Error: stdin contains binary data (not valid UTF-8)", err=True)
            raise typer.Exit(1)

    # Determine if we're getting or setting
    setting = content is not None or reset

    if setting:
        if reset:
            # Reset to default from system (delete first to clear old tags)
            from .api import _load_frontmatter, SYSTEM_DOC_DIR
            kp.delete(doc_id)
            try:
                new_content, default_tags = _load_frontmatter(SYSTEM_DOC_DIR / "now.md")
                parsed_tags = default_tags
            except FileNotFoundError:
                typer.echo("Error: Builtin now.md not found", err=True)
                raise typer.Exit(1)
        else:
            new_content = content
            parsed_tags = {}

        # Parse user-provided tags (merge with default if reset)
        parsed_tags.update(_parse_tags(tags))

        item = kp.set_now(new_content, scope=scope, tags=parsed_tags or None)

        # Surface similar items and meta sections (occasion for reflection)
        similar_items = kp.get_similar_for_display(item.id, limit=limit)
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(item.id, limit_per_doc=limit)

        typer.echo(_format_item(
            item,
            as_json=_get_json_output(),
            similar_items=similar_items if similar_items else None,
            similar_offsets=similar_offsets if similar_items else None,
            meta_sections=meta_sections if meta_sections else None,
        ))
    else:
        # Get current intentions (or search version history if tags specified)
        if tags:
            # Search version history for most recent version with matching tags
            item = _find_now_version_by_tags(kp, tags, scope=scope)
            if item is None:
                typer.echo("No version found matching tags", err=True)
                raise typer.Exit(1)
            # No version nav or similar items for filtered results
            typer.echo(_format_item(item, as_json=_get_json_output()))
        else:
            # Standard: get current with version navigation and similar items
            item = kp.get_now(scope=scope)
            version_nav = kp.get_version_nav(doc_id, None)
            similar_items = kp.get_similar_for_display(doc_id, limit=limit)
            similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
            meta_sections = kp.resolve_meta(doc_id, limit_per_doc=limit)
            typer.echo(_format_item(
                item,
                as_json=_get_json_output(),
                version_nav=version_nav,
                similar_items=similar_items,
                similar_offsets=similar_offsets,
                meta_sections=meta_sections,
            ))


def _find_now_version_by_tags(kp, tags: list[str], *, scope: Optional[str] = None):
    """
    Search nowdoc version history for most recent version matching all tags.

    Checks current version first, then scans previous versions.
    """
    from .api import NOWDOC_ID
    doc_id = f"now:{scope}" if scope else NOWDOC_ID

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
    current = kp.get_now(scope=scope)
    if current and matches_tags(current.tags):
        return current

    # Scan previous versions (newest first)
    versions = kp.list_versions(doc_id, limit=100)
    for i, v in enumerate(versions):
        if matches_tags(v.tags):
            # Found match - get full item at this version offset
            return kp.get_version(doc_id, i + 1)

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
def move(
    name: Annotated[str, typer.Argument(help="Target note name")],
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Only extract versions matching these tags (key=value)"
    )] = None,
    from_source: Annotated[Optional[str], typer.Option(
        "--from",
        help="Source note to extract from (default: now)"
    )] = None,
    only: Annotated[bool, typer.Option(
        "--only",
        help="Move only the current (tip) version"
    )] = False,
    do_analyze: Annotated[bool, typer.Option(
        "--analyze",
        help="Queue background analysis after move"
    )] = False,
    store: StoreOption = None,
):
    """
    Move versions from now (or another item) into a named item.

    Requires either -t (tag filter) or --only (tip only).
    With -t, matching versions are extracted from the source.
    With --only, just the current version is moved.
    With --from, extract from a specific item instead of now.
    """
    if not tags and not only:
        typer.echo(
            "Error: use -t to filter by tags, or --only to move just the current version",
            err=True,
        )
        raise typer.Exit(1)

    kp = _get_keeper(store)
    tag_filter = _parse_tags(tags) if tags else None
    source_id = from_source if from_source else None

    try:
        kwargs: dict = {"tags": tag_filter, "only_current": only}
        if source_id:
            kwargs["source_id"] = source_id
        saved = kp.move(name, **kwargs)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    as_json = _get_json_output()
    versions = kp.list_versions(name, limit=100)
    items = _versions_to_items(name, saved, versions)
    typer.echo(_format_items(items, as_json=as_json))

    if do_analyze:
        try:
            kp.enqueue_analyze(name)
            typer.echo(f"Queued {name} for background analysis.", err=True)
        except ValueError:
            pass


@app.command()
def get(
    id: Annotated[list[str], typer.Argument(help="URI(s) of note(s) (append @V{N} for version)")],
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
        help="List similar notes"
    )] = False,
    meta: Annotated[bool, typer.Option(
        "--meta", "-M",
        help="List meta notes"
    )] = False,
    resolve: Annotated[Optional[list[str]], typer.Option(
        "--resolve", "-R",
        help="Inline meta query (metadoc syntax, repeatable)"
    )] = None,
    parts: Annotated[bool, typer.Option(
        "--parts", "-P",
        help="List structural parts (from analyze)"
    )] = False,
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Require tag (key or key=value, repeatable)"
    )] = None,
    limit: Annotated[int, typer.Option(
        "--limit", "-n",
        help="Max notes for --history, --similar, or --meta (default: 10)"
    )] = 10,
    store: StoreOption = None,
):
    """
    Retrieve note(s) by ID.

    Accepts one or more IDs. Version identifiers: Append @V{N} to get a specific version.
    Part identifiers: Append @P{N} to get a specific part.

    \b
    Examples:
        keep get doc:1                  # Current version with similar notes
        keep get doc:1 doc:2 doc:3      # Multiple notes
        keep get doc:1 -V 1             # Previous version with prev/next nav
        keep get "doc:1@V{1}"           # Same as -V 1
        keep get "doc:1@P{1}"           # Part 1 of analyzed note
        keep get doc:1 --history        # List all versions
        keep get doc:1 --parts          # List structural parts
        keep get doc:1 --similar        # List similar items
        keep get doc:1 --meta           # List meta items
        keep get doc:1 -t project=myapp # Only if tag matches
    """
    kp = _get_keeper(store)
    outputs = []
    errors = []

    for one_id in id:
        result = _get_one(kp, one_id, version, history, similar, meta, resolve, tag, limit, parts)
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
    meta: bool,
    resolve: Optional[list[str]],
    tag: Optional[list[str]],
    limit: int,
    show_parts: bool = False,
) -> Optional[str]:
    """Get a single item and return its formatted output, or None on error."""

    # Parse @V{N} or @P{N} identifier from ID (security: check literal first)
    actual_id = one_id
    version_from_id = None
    part_from_id = None

    if kp.exists(one_id):
        # Literal ID exists - use it directly (prevents confusion attacks)
        actual_id = one_id
    else:
        # Try parsing @P{N} suffix first
        match = PART_SUFFIX_PATTERN.search(one_id)
        if match:
            part_from_id = int(match.group(1))
            actual_id = one_id[:match.start()]
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

    # Part addressing: return part directly
    if part_from_id is not None:
        item = kp.get_part(actual_id, part_from_id)
        if item is None:
            typer.echo(f"Part not found: {actual_id}@P{{{part_from_id}}}", err=True)
            return None

        if _get_ids_output():
            return f"{_shell_quote_id(actual_id)}@P{{{part_from_id}}}"
        if _get_json_output():
            return json.dumps({
                "id": actual_id,
                "part": part_from_id,
                "total_parts": int(item.tags.get("_total_parts", 0)),
                "summary": item.summary,
                "tags": _filter_display_tags(item.tags),
            }, indent=2)

        # Build part navigation
        total = int(item.tags.get("_total_parts", 0))
        lines = ["---", f"id: {_shell_quote_id(actual_id)}@P{{{part_from_id}}}"]
        display_tags = _filter_display_tags(item.tags)
        if display_tags:
            tag_items = ", ".join(f"{k}: {v}" for k, v in sorted(display_tags.items()))
            lines.append(f"tags: {{{tag_items}}}")
        # Part navigation
        if part_from_id > 1:
            lines.append("prev:")
            lines.append(f"  - @P{{{part_from_id - 1}}}")
        if part_from_id < total:
            lines.append("next:")
            lines.append(f"  - @P{{{part_from_id + 1}}}")
        lines.append("---")
        lines.append(item.summary)
        return "\n".join(lines)

    if history:
        # List all versions
        versions = kp.list_versions(actual_id, limit=limit)
        current = kp.get(actual_id)
        items = _versions_to_items(actual_id, current, versions)
        return _format_items(items, as_json=_get_json_output())

    if show_parts:
        # List all parts
        part_list = kp.list_parts(actual_id)
        if _get_ids_output():
            return "\n".join(f"{actual_id}@P{{{p.part_num}}}" for p in part_list)
        elif _get_json_output():
            result = {
                "id": actual_id,
                "parts": [
                    {
                        "part": p.part_num,
                        "pid": f"{actual_id}@P{{{p.part_num}}}",
                        "summary": p.summary[:100],
                        "tags": {k: v for k, v in p.tags.items() if not k.startswith("_")},
                    }
                    for p in part_list
                ],
            }
            return json.dumps(result, indent=2)
        else:
            if not part_list:
                return f"No parts for {actual_id}. Use 'keep analyze {actual_id}' to create parts."
            lines = [f"Parts for {actual_id}:"]
            for p in part_list:
                summary_preview = p.summary[:60].replace("\n", " ")
                if len(p.summary) > 60:
                    summary_preview += "..."
                lines.append(f"  @P{{{p.part_num}}} {summary_preview}")
            return "\n".join(lines)

    if similar:
        # List similar items
        similar_items = kp.get_similar_for_display(actual_id, limit=limit)
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
                        "date": local_date(item.tags.get("_updated") or item.tags.get("_created", "")),
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
                    date_part = local_date(item.tags.get("_updated") or item.tags.get("_created", ""))
                    summary_preview = item.summary[:50].replace("\n", " ")
                    if len(item.summary) > 50:
                        summary_preview += "..."
                    lines.append(f"  {base_id}@V{{{offset}}} {score_str} {date_part} {summary_preview}")
            else:
                lines.append("  No similar notes found.")
            return "\n".join(lines)

    if meta:
        # List meta items for this ID
        meta_sections = kp.resolve_meta(actual_id, limit_per_doc=limit)
        if _get_ids_output():
            lines = []
            for name, items in meta_sections.items():
                for item in items:
                    lines.append(_shell_quote_id(item.id))
            return "\n".join(lines)
        elif _get_json_output():
            result = {
                "id": actual_id,
                "meta": {
                    name: [{"id": item.id, "summary": item.summary[:60]} for item in items]
                    for name, items in meta_sections.items()
                },
            }
            return json.dumps(result, indent=2)
        else:
            lines = [f"Meta for {actual_id}:"]
            for name, items in meta_sections.items():
                lines.append(f"  {name}:")
                for item in items:
                    summary_preview = item.summary[:50].replace("\n", " ")
                    if len(item.summary) > 50:
                        summary_preview += "..."
                    lines.append(f"    {_shell_quote_id(item.id)}  {summary_preview}")
            if len(lines) == 1:
                lines.append("  No meta notes found.")
            return "\n".join(lines)

    if resolve:
        # Inline meta-resolve: parse metadoc-syntax strings, union results
        from .api import _parse_meta_doc
        all_queries: list[dict[str, str]] = []
        all_context: list[str] = []
        all_prereqs: list[str] = []
        for r in resolve:
            q, c, p = _parse_meta_doc(r)
            all_queries.extend(q)
            all_context.extend(c)
            all_prereqs.extend(p)
        # Deduplicate context/prereq keys
        all_context = list(dict.fromkeys(all_context))
        all_prereqs = list(dict.fromkeys(all_prereqs))
        items = kp.resolve_inline_meta(
            actual_id, all_queries, all_context, all_prereqs, limit=limit,
        )
        if _get_ids_output():
            return "\n".join(_shell_quote_id(item.id) for item in items)
        elif _get_json_output():
            result = {
                "id": actual_id,
                "resolve": [{"id": item.id, "summary": item.summary[:60]} for item in items],
            }
            return json.dumps(result, indent=2)
        else:
            lines = [f"Resolve for {actual_id}:"]
            for item in items:
                summary_preview = item.summary[:50].replace("\n", " ")
                if len(item.summary) > 50:
                    summary_preview += "..."
                lines.append(f"  {_shell_quote_id(item.id)}  {summary_preview}")
            if len(lines) == 1:
                lines.append("  No matching notes found.")
            return "\n".join(lines)

    # Get specific version or current
    offset = effective_version if effective_version is not None else 0

    if offset == 0:
        item = kp.get(actual_id)
        internal_version = None
    else:
        item = kp.get_version(actual_id, offset)
        # Calculate internal version number for API call
        versions = kp.list_versions(actual_id, limit=1)
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
    version_nav = kp.get_version_nav(actual_id, internal_version)

    # Get similar items, meta sections, and parts manifest for current version
    similar_items = None
    similar_offsets = None
    meta_sections = None
    parts_manifest = None
    if offset == 0:
        similar_items = kp.get_similar_for_display(actual_id, limit=3)
        similar_offsets = {s.id: kp.get_version_offset(s) for s in similar_items}
        meta_sections = kp.resolve_meta(actual_id)
        parts = kp.list_parts(actual_id)
        if parts:
            parts_manifest = parts

    return _format_item(
        item,
        as_json=_get_json_output(),
        version_nav=version_nav,
        viewing_offset=offset if offset > 0 else None,
        similar_items=similar_items,
        similar_offsets=similar_offsets,
        meta_sections=meta_sections,
        parts_manifest=parts_manifest,
    )


@app.command("del")
def del_cmd(
    id: Annotated[list[str], typer.Argument(help="ID(s) of note(s) to delete")],
    store: StoreOption = None,
):
    """
    Delete the current version of note(s).

    If a note has version history, reverts to the previous version.
    If no history exists, removes the note completely.

    \b
    Examples:
        keep del %abc123def456        # Remove a text note
        keep del %abc123 %def456      # Remove multiple notes
        keep del now                  # Revert now to previous
    """
    kp = _get_keeper(store)
    had_errors = False

    for one_id in id:
        # Parts cannot be individually deleted
        if PART_SUFFIX_PATTERN.search(one_id):
            typer.echo(f"Error: cannot delete individual parts. Re-analyze or delete the parent.", err=True)
            had_errors = True
            continue

        item = kp.get(one_id)
        if item is None:
            typer.echo(f"Not found: {one_id}", err=True)
            had_errors = True
            continue

        restored = kp.revert(one_id)

        if restored is None:
            # Fully deleted
            typer.echo(_format_summary_line(item))
        else:
            # Reverted — show the restored version with similar items
            similar_items = kp.get_similar_for_display(restored.id, limit=3)
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
    id: Annotated[list[str], typer.Argument(help="ID(s) of note(s) to delete")],
    store: StoreOption = None,
):
    """Delete the current version of note(s) (alias for 'del')."""
    del_cmd(id=id, store=store)


@app.command()
def analyze(
    id: Annotated[str, typer.Argument(help="ID of note to analyze into parts")],
    tag: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Guidance tag keys for decomposition (e.g., -t topic -t type)",
    )] = None,
    foreground: Annotated[bool, typer.Option(
        "--foreground", "--fg",
        help="Run in foreground (default: background)"
    )] = False,
    force: Annotated[bool, typer.Option(
        "--force",
        help="Re-analyze even if parts are already current"
    )] = False,
    store: StoreOption = None,
):
    """
    Decompose a note or string into meaningful parts.

    For documents (URI sources): decomposes content structurally.
    For inline notes (strings): assembles version history and decomposes
    the temporal sequence into episodic parts.

    Uses an LLM to identify sections, each with its own summary, tags,
    and embedding. Parts appear in 'find' results and can be accessed
    with @P{N} syntax.

    Skips analysis if parts are already current (content unchanged since
    last analysis). Use --force to re-analyze regardless.

    Runs in the background by default (serialized with other ML work);
    use --fg to wait for results.
    """
    kp = _get_keeper(store)

    # Background mode (default): enqueue for serial processing
    if not foreground:
        try:
            enqueued = kp.enqueue_analyze(id, tags=tag, force=force)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

        if not enqueued:
            if _get_json_output():
                typer.echo(json.dumps({"id": id, "status": "skipped"}))
            else:
                typer.echo(f"Already analyzed, skipping {id}.", err=True)
            kp.close()
            return

        if _get_json_output():
            typer.echo(json.dumps({"id": id, "status": "queued"}))
        else:
            typer.echo(f"Queued {id} for background analysis.", err=True)
        kp.close()
        return

    try:
        parts = kp.analyze(id, tags=tag, force=force)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Analysis failed: {e}", err=True)
        raise typer.Exit(1)

    if not parts:
        if _get_json_output():
            typer.echo(json.dumps({"id": id, "parts": []}))
        else:
            typer.echo(f"Content not decomposable into multiple parts: {id}")
        return

    if _get_json_output():
        result = {
            "id": id,
            "parts": [
                {
                    "part": p.part_num,
                    "pid": f"{id}@P{{{p.part_num}}}",
                    "summary": p.summary[:100],
                    "tags": {k: v for k, v in p.tags.items() if not k.startswith("_")},
                }
                for p in parts
            ],
        }
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"Analyzed {id} into {len(parts)} parts:")
        for p in parts:
            summary_preview = p.summary[:60].replace("\n", " ")
            if len(p.summary) > 60:
                summary_preview += "..."
            typer.echo(f"  @P{{{p.part_num}}} {summary_preview}")





def _get_config_value(cfg, store_path: Path, path: str):
    """
    Get config value by dotted path.

    Special paths (not in TOML):
        file - config file location
        tool - package directory (SKILL.md location)
        openclaw-plugin - OpenClaw plugin directory
        store - store path

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
    if path == "docs":
        return str(get_tool_directory() / "docs")
    if path == "store":
        return str(store_path)
    # Provider shortcuts
    if path == "providers":
        if cfg:
            return {
                "embedding": cfg.embedding.name if cfg.embedding else None,
                "summarization": cfg.summarization.name,
                "document": cfg.document.name,
            }
        return None
    if path == "providers.embedding":
        return cfg.embedding.name if cfg and cfg.embedding else None
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

    # Show paths
    lines.append(f"file: {config_path}")
    lines.append(f"tool: {get_tool_directory()}")
    lines.append(f"docs: {get_tool_directory() / 'docs'}")
    lines.append(f"store: {store_path}")
    import importlib.resources
    lines.append(f"openclaw-plugin: {Path(str(importlib.resources.files('keep'))) / 'data' / 'openclaw-plugin'}")

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
        lines.append("#   GOOGLE_CLOUD_PROJECT → Vertex AI (uses Workload Identity / ADC)")
        lines.append("#")
        lines.append("# Models (configure in keep.toml):")
        lines.append("#   voyage: voyage-3.5-lite (default), voyage-3-large, voyage-code-3")
        lines.append("#   anthropic: claude-3-haiku-20240307 (default), claude-3-5-haiku-20241022")
        lines.append("#   openai embedding: text-embedding-3-small (default), text-embedding-3-large")
        lines.append("#   openai summarization: gpt-4o-mini (default)")
        lines.append("#   gemini embedding: text-embedding-004 (default)")
        lines.append("#   gemini summarization: gemini-2.5-flash (default)")
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
        keep config docs         # Documentation directory
        keep config openclaw-plugin  # OpenClaw plugin directory
        keep config store        # Store path
        keep config providers    # All provider config
        keep config providers.embedding  # Embedding provider name
        keep config --reset-system-docs  # Reset bundled system docs
    """
    # Handle system docs reset - requires full Keeper initialization
    if reset_system_docs:
        kp = _get_keeper(store)
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
        import importlib.resources
        result = {
            "file": str(config_path) if config_path else None,
            "tool": str(get_tool_directory()),
            "docs": str(get_tool_directory() / "docs"),
            "store": str(store_path),
            "openclaw-plugin": str(Path(str(importlib.resources.files("keep"))) / "data" / "openclaw-plugin"),
            "providers": {
                "embedding": cfg.embedding.name if cfg and cfg.embedding else None,
                "summarization": cfg.summarization.name if cfg else None,
                "document": cfg.document.name if cfg else None,
            },
        }
        if cfg and cfg.default_tags:
            result["tags"] = cfg.default_tags
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(_format_config_with_defaults(cfg, store_path))


@app.command("pending")
def pending_cmd(
    store: StoreOption = None,
    reindex: Annotated[bool, typer.Option(
        "--reindex",
        help="Enqueue all items for re-embedding, then process"
    )] = False,
    stop: Annotated[bool, typer.Option(
        "--stop",
        help="Stop the background processor"
    )] = False,
    daemon: Annotated[bool, typer.Option(
        "--daemon",
        hidden=True,
        help="Run as background daemon (used internally)"
    )] = False,
):
    """
    Process pending background tasks.

    Starts a background processor (if not already running) and shows
    progress. Ctrl-C detaches without stopping the processor.
    Use --reindex to re-embed all items with the current embedding provider.
    """
    import signal
    kp = _get_keeper(store)

    # --stop: send SIGTERM to the daemon
    if stop:
        pid_path = kp._processor_pid_path
        if not kp._is_processor_running():
            typer.echo("No processor running.")
            pid_path.unlink(missing_ok=True)
            kp.close()
            return
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                typer.echo(f"Sent stop signal to processor (pid {pid}).", err=True)
            except (ProcessLookupError, ValueError):
                typer.echo("Processor not running (stale PID file).", err=True)
                pid_path.unlink(missing_ok=True)
        else:
            typer.echo("Processor running but no PID file found.", err=True)
        kp.close()
        return

    # --daemon: run as the actual background processor
    if daemon:
        import logging
        from .model_lock import ModelLock

        _daemon_logger = logging.getLogger("keep.cli.daemon")
        pid_path = kp._processor_pid_path
        processor_lock = ModelLock(kp._store_path / ".processor.lock")
        shutdown_requested = False

        if not processor_lock.acquire(blocking=False):
            _daemon_logger.info("Daemon: another processor already running, exiting")
            kp.close()
            return

        _daemon_logger.info("Daemon started (pid=%d)", os.getpid())

        def handle_signal(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            pid_path.write_text(str(os.getpid()))
            while not shutdown_requested:
                result = kp.process_pending(limit=50)
                _daemon_logger.info(
                    "Daemon batch: processed=%d failed=%d",
                    result["processed"], result["failed"],
                )
                if result["processed"] == 0 and result["failed"] == 0:
                    break
        finally:
            _daemon_logger.info("Daemon shutting down")
            try:
                pid_path.unlink()
            except OSError:
                pass
            kp.close()
            processor_lock.release()
        return

    # --reindex: enqueue all items for re-embedding
    if reindex:
        count = kp.count()
        if count == 0:
            typer.echo("No notes to reindex.")
            kp.close()
            raise typer.Exit(0)
        typer.echo(f"Enqueuing {count} notes for reindex...", err=True)
        stats = kp.enqueue_reindex()
        typer.echo(
            f"Enqueued {stats['enqueued']} items + {stats['versions']} versions",
            err=True,
        )

    # Interactive mode: show status, ensure daemon running, tail log
    pending_count = kp.pending_count()

    if pending_count == 0:
        typer.echo("Nothing pending.")
        kp.close()
        return

    by_type = kp.pending_stats_by_type()
    parts = [f"{count} {ttype}" for ttype, count in sorted(by_type.items())]
    typer.echo(f"Queue: {pending_count} items ({', '.join(parts)})", err=True)

    # Ensure daemon is running
    if not kp._is_processor_running():
        kp._spawn_processor()
        typer.echo("Started background processor.", err=True)
    else:
        typer.echo("Background processor already running.", err=True)

    # Tail the ops log until daemon finishes or user Ctrl-C's
    log_path = kp._store_path / "keep-ops.log"
    _tail_ops_log(log_path, kp)
    kp.close()


def _tail_ops_log(log_path: Path, kp) -> None:
    """Tail the ops log, showing new lines until daemon finishes or Ctrl-C."""
    import time

    # Grace period for daemon startup (takes a moment to acquire lock)
    time.sleep(1.0)

    try:
        # Ensure log file exists (may not on fresh stores)
        log_path.touch(exist_ok=True)
        with open(log_path) as f:
            f.seek(0, 2)  # Seek to end
            idle_checks = 0
            while True:
                line = f.readline()
                if line:
                    typer.echo(line.rstrip(), err=True)
                    idle_checks = 0
                else:
                    # No new line — check if daemon is still running
                    idle_checks += 1
                    if idle_checks >= 5 and not kp._is_processor_running():
                        remaining = kp.pending_count()
                        if remaining == 0:
                            typer.echo("Done.", err=True)
                        else:
                            typer.echo(f"{remaining} items remaining.", err=True)
                        break
                    time.sleep(0.5)
    except (KeyboardInterrupt, EOFError):
        remaining = kp.pending_count()
        typer.echo(
            f"\nDetached. Daemon still running. {remaining} remaining.",
            err=True,
        )


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


@app.command(hidden=True)
def doctor(
    store: StoreOption = None,
    use_faulthandler: Annotated[bool, typer.Option(
        "--faulthandler", help="Enable faulthandler for native crash traces"
    )] = False,
):
    """Diagnostic checks for debugging setup and crash issues."""
    import platform
    import time

    if use_faulthandler:
        import faulthandler
        faulthandler.enable()
        typer.echo("faulthandler enabled (native crash traces will print to stderr)\n")

    def ok(msg):
        typer.echo(f"  [ok]   {msg}")

    def fail(msg):
        typer.echo(f"  [FAIL] {msg}")

    # 1. Environment
    from importlib.metadata import version as pkg_version
    try:
        kv = pkg_version("keep-skill")
    except Exception:
        kv = "?"
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    plat = f"{platform.system()} {platform.release()} ({platform.machine()})"
    ok(f"Environment: Python {py_ver}, {plat}, keep-skill {kv}")

    # 2. Key packages
    pkg_names = ["chromadb", "sentence_transformers", "torch", "pydantic", "sqlite3"]
    pkg_versions = {}
    for pkg in pkg_names:
        try:
            mod = __import__(pkg)
            pkg_versions[pkg] = getattr(mod, "__version__", getattr(mod, "version", "imported"))
        except ImportError:
            pkg_versions[pkg] = "not installed"
    pkg_str = ", ".join(f"{k} {v}" for k, v in pkg_versions.items())
    ok(f"Packages: {pkg_str}")

    # 3. Store config
    from .config import load_or_create_config
    from .paths import get_config_dir, get_default_store_path
    try:
        actual_store = store if store is not None else _get_store_override()
        config_dir = Path(actual_store).resolve() if actual_store else get_config_dir()
        cfg = load_or_create_config(config_dir)
        store_path = Path(get_default_store_path(cfg) if actual_store is None else actual_store)
        emb_name = cfg.embedding.name if cfg and cfg.embedding else "none"
        sum_name = cfg.summarization.name if cfg and cfg.summarization else "none"
        ok(f"Store config: {store_path} (embedding: {emb_name}, summarization: {sum_name})")
    except Exception as e:
        fail(f"Store config: {e}")
        store_path = None

    # 4. SQLite (DocumentStore)
    db_path = store_path / "documents.db" if store_path else None
    if db_path and db_path.exists():
        try:
            from .document_store import DocumentStore
            ds = DocumentStore(db_path)
            doc_count = ds.count("default")
            ver_count = ds.count_versions("default")
            ok(f"SQLite: {doc_count} documents, {ver_count} versions")
            ds.close()
        except Exception as e:
            fail(f"SQLite: {e}")
    elif store_path:
        ok("SQLite: no documents.db yet (new store)")
    else:
        fail("SQLite: skipped (no store path)")

    # 5. Embedding provider
    if cfg and cfg.embedding:
        try:
            from .providers.base import get_registry
            registry = get_registry()
            provider = registry.create_embedding(cfg.embedding.name, cfg.embedding.params)
            t0 = time.perf_counter()
            vec = provider.embed("keep doctor test")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            dim = len(vec)
            model = getattr(provider, "model_name", "?")
            ok(f"Embedding: {model}, dim={dim}, {elapsed_ms:.0f}ms")
        except Exception as e:
            fail(f"Embedding: {e}")
    else:
        ok("Embedding: no provider configured")

    # 6. Summarization provider
    if cfg and cfg.summarization and cfg.summarization.name != "passthrough":
        try:
            from .providers.base import get_registry
            registry = get_registry()
            provider = registry.create_summarization(cfg.summarization.name, cfg.summarization.params)
            t0 = time.perf_counter()
            result = provider.summarize("The quick brown fox jumps over the lazy dog.")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            model = getattr(provider, "model_name", cfg.summarization.name)
            ok(f"Summarization: {model}, {elapsed_ms:.0f}ms")
        except Exception as e:
            fail(f"Summarization: {e}")
    else:
        ok(f"Summarization: {'passthrough' if cfg and cfg.summarization else 'none'}")

    # 7. Media describer
    if cfg and cfg.media:
        try:
            from .providers.base import get_registry
            registry = get_registry()
            provider = registry.create_media(cfg.media.name, cfg.media.params)
            model = getattr(provider, "model", getattr(provider, "model_name", cfg.media.name))
            ok(f"Media: {cfg.media.name} ({model})")
        except Exception as e:
            fail(f"Media: {e}")
    else:
        ok("Media: none configured (metadata-only indexing)")

    # 8. Analyzer
    if cfg and cfg.analyzer:
        try:
            from .providers.base import get_registry
            registry = get_registry()
            provider = registry.create_analyzer(cfg.analyzer.name, cfg.analyzer.params)
            ok(f"Analyzer: {cfg.analyzer.name}")
        except Exception as e:
            fail(f"Analyzer: {e}")
    else:
        ok("Analyzer: default (uses summarization provider)")

    # 9. ChromaDB
    if store_path and (store_path / "chroma").exists():
        try:
            from .store import ChromaStore
            cs = ChromaStore(store_path)
            collections = cs.list_collections()
            counts = {c: cs.count(c) for c in collections}
            parts = [f"{c} ({n})" for c, n in counts.items()]
            ok(f"ChromaDB: {', '.join(parts) if parts else 'no collections'}")
        except Exception as e:
            fail(f"ChromaDB: {e}")
    elif store_path:
        ok("ChromaDB: no chroma/ yet (new store)")
    else:
        fail("ChromaDB: skipped (no store path)")

    # 10. Model locks
    if store_path:
        from .model_lock import ModelLock
        for lock_name in [".embedding.lock", ".summarization.lock"]:
            lock_file = store_path / lock_name
            if lock_file.exists():
                probe = ModelLock(lock_file)
                if probe.is_locked():
                    fail(f"Lock: {lock_name} is held by another process (lsof {lock_file})")
                else:
                    ok(f"Lock: {lock_name} available")
            else:
                ok(f"Lock: {lock_name} (not yet created)")
    else:
        ok("Lock: skipped (no store path)")

    # 11. Round-trip (temp store, isolates stack from store data)
    import tempfile
    import shutil
    from .config import StoreConfig, ProviderConfig
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="keep_doctor_")
        tmp_path = Path(tmp_dir)
        # Minimal config: passthrough summarization (no LLM)
        test_config = StoreConfig(
            path=tmp_path,
            summarization=ProviderConfig("passthrough", {"max_chars": 10000}),
            max_summary_length=10000,
        )
        t0 = time.perf_counter()
        kp = Keeper(tmp_dir, config=test_config)
        kp.put("The quick brown fox jumps over the lazy dog", id="doctor_test")
        item = kp.get("doctor_test")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if item and "fox" in item.summary:
            ok(f"Round-trip: put + get in {elapsed_ms:.0f}ms")
        else:
            fail("Round-trip: put succeeded but get returned unexpected result")
    except Exception as e:
        fail(f"Round-trip: {e}")
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    typer.echo()


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
