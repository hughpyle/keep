"""
CLI interface for associative memory.

Usage:
    keepfind "query text"
    keepupdate file:///path/to/doc.md
    keepget file:///path/to/doc.md
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from .api import Keeper
from .types import Item
from .logging_config import configure_quiet_mode, enable_debug_mode


# Configure quiet mode by default (suppress verbose library output)
# Set KEEP_VERBOSE=1 to enable debug mode via environment
if os.environ.get("KEEP_VERBOSE") == "1":
    enable_debug_mode()
else:
    configure_quiet_mode(quiet=True)


def _verbose_callback(value: bool):
    if value:
        enable_debug_mode()


# Global state for CLI options
_json_output = False
_ids_output = False


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


app = typer.Typer(
    name="keep",
    help="Associative memory with semantic search.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _format_yaml_frontmatter(item: Item) -> str:
    """Format item as YAML frontmatter with summary as content."""
    lines = ["---", f"id: {item.id}"]
    if item.tags:
        lines.append("tags:")
        for k, v in sorted(item.tags.items()):
            lines.append(f"  {k}: {v}")
    if item.score is not None:
        lines.append(f"score: {item.score:.3f}")
    lines.append("---")
    lines.append(item.summary)  # Summary IS the content
    return "\n".join(lines)


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
):
    """Associative memory with semantic search."""
    # If no subcommand provided, show the current context (now)
    if ctx.invoked_subcommand is None:
        kp = _get_keeper(None, "default")
        item = kp.get_now()
        typer.echo(_format_item(item, as_json=_get_json_output()))
        if not _get_json_output():
            typer.echo("\nUse --help for commands.", err=True)


# -----------------------------------------------------------------------------
# Common Options
# -----------------------------------------------------------------------------

StoreOption = Annotated[
    Optional[Path],
    typer.Option(
        "--store", "-s",
        envvar="KEEP_STORE_PATH",
        help="Path to the store directory (default: .keep/ at repo root)"
    )
]

CollectionOption = Annotated[
    str,
    typer.Option(
        "--collection", "-c",
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


# -----------------------------------------------------------------------------
# Output Helpers
# -----------------------------------------------------------------------------

def _format_item(item: Item, as_json: bool = False) -> str:
    """
    Format an item for display.

    Text format: YAML frontmatter (matches docs/system format)
    With --ids: just the ID (for piping)
    """
    if _get_ids_output():
        return json.dumps(item.id) if as_json else item.id

    if as_json:
        return json.dumps({
            "id": item.id,
            "summary": item.summary,
            "tags": item.tags,
            "score": item.score,
        })

    return _format_yaml_frontmatter(item)


def _format_items(items: list[Item], as_json: bool = False) -> str:
    """Format multiple items for display."""
    if _get_ids_output():
        ids = [item.id for item in items]
        return json.dumps(ids) if as_json else "\n".join(ids)

    if as_json:
        return json.dumps([
            {
                "id": item.id,
                "summary": item.summary,
                "tags": item.tags,
                "score": item.score,
            }
            for item in items
        ], indent=2)
    else:
        if not items:
            return "No results."
        return "\n\n".join(_format_item(item, as_json=False) for item in items)


def _get_keeper(store: Optional[Path], collection: str) -> Keeper:
    """Initialize memory, handling errors gracefully."""
    # store=None is fine — Keeper will use default (git root/.keep)
    try:
        return Keeper(store, collection=collection)
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


def _timestamp() -> str:
    """Generate timestamp for auto-generated IDs."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


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
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    since: SinceOption = None,
):
    """
    Find items using semantic similarity search.

    Examples:
        keep find "authentication"              # Search by text
        keep find --id file:///path/to/doc.md   # Find similar to item
    """
    if id and query:
        typer.echo("Error: Specify either a query or --id, not both", err=True)
        raise typer.Exit(1)
    if not id and not query:
        typer.echo("Error: Specify a query or --id", err=True)
        raise typer.Exit(1)

    kp = _get_keeper(store, collection)

    if id:
        results = kp.find_similar(id, limit=limit, since=since, include_self=include_self)
    else:
        results = kp.find(query, limit=limit, since=since)

    typer.echo(_format_items(results, as_json=_get_json_output()))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Full-text search query")],
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


@app.command()
def tag(
    query: Annotated[Optional[str], typer.Argument(
        help="Tag key to list values, or key=value to find docs"
    )] = None,
    list_keys: Annotated[bool, typer.Option(
        "--list", "-l",
        help="List all distinct tag keys"
    )] = False,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 100,
    since: SinceOption = None,
):
    """
    List tag values or find items by tag.

    Examples:
        keep tag --list              # List all tag keys
        keep tag project             # List values for 'project' tag
        keep tag project=myapp       # Find docs with project=myapp
    """
    kp = _get_keeper(store, collection)

    # List all keys mode
    if list_keys or query is None:
        tags = kp.list_tags(None, collection=collection)
        if _get_json_output():
            typer.echo(json.dumps(tags))
        else:
            if not tags:
                typer.echo("No tags found.")
            else:
                for t in tags:
                    typer.echo(t)
        return

    # Check if query is key=value or just key
    if "=" in query:
        # key=value → find documents
        key, value = query.split("=", 1)
        results = kp.query_tag(key, value, limit=limit, since=since)
        typer.echo(_format_items(results, as_json=_get_json_output()))
    else:
        # key only → list values
        values = kp.list_tags(query, collection=collection)
        if _get_json_output():
            typer.echo(json.dumps(values))
        else:
            if not values:
                typer.echo(f"No values for tag '{query}'.")
            else:
                for v in values:
                    typer.echo(v)


@app.command("tag-update")
def tag_update(
    ids: Annotated[list[str], typer.Argument(help="Document IDs to tag")],
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

    Examples:
        keep tag-update doc:1 --tag project=myapp
        keep tag-update doc:1 doc:2 --tag status=reviewed
        keep tag-update doc:1 --remove obsolete_tag
        keep tag-update doc:1 --tag temp=  # Remove via empty value
    """
    kp = _get_keeper(store, collection)

    # Parse tags from key=value format
    tag_changes: dict[str, str] = {}
    if tags:
        for tag in tags:
            if "=" not in tag:
                typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value (or key= to remove)", err=True)
                raise typer.Exit(1)
            k, v = tag.split("=", 1)
            tag_changes[k] = v  # Empty v means delete

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

    if _get_json_output():
        typer.echo(_format_items(results, as_json=True))
    else:
        for item in results:
            typer.echo(_format_item(item, as_json=False))


@app.command()
def update(
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
    lazy: Annotated[bool, typer.Option(
        "--lazy",
        help="Fast mode: use truncated summary, queue for later processing"
    )] = False,
):
    """
    Add or update a document in the store.

    Three input modes (auto-detected):
      keep update file:///path       # URI mode: has ://
      keep update "my note"          # Text mode: no ://
      keep update -                  # Stdin mode: explicit -
      echo "pipe" | keep update      # Stdin mode: piped input
    """
    kp = _get_keeper(store, collection)
    parsed_tags = _parse_tags(tags)

    # Determine mode based on source content
    if source == "-" or (source is None and not sys.stdin.isatty()):
        # Stdin mode: explicit '-' or piped input
        content = sys.stdin.read()
        content, frontmatter_tags = _parse_frontmatter(content)
        parsed_tags = {**frontmatter_tags, **parsed_tags}  # CLI tags override
        doc_id = id or f"mem:{_timestamp()}"
        item = kp.remember(content, id=doc_id, summary=summary, tags=parsed_tags or None, lazy=lazy)
    elif source and "://" in source:
        # URI mode: fetch from URI (ID is the URI itself)
        item = kp.update(source, tags=parsed_tags or None, summary=summary, lazy=lazy)
    elif source:
        # Text mode: inline content (no :// in source)
        doc_id = id or f"mem:{_timestamp()}"
        item = kp.remember(source, id=doc_id, summary=summary, tags=parsed_tags or None, lazy=lazy)
    else:
        typer.echo("Error: Provide content, URI, or '-' for stdin", err=True)
        raise typer.Exit(1)

    typer.echo(_format_item(item, as_json=_get_json_output()))


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
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Tag as key=value (can be repeated)"
    )] = None,
):
    """
    Get or set the current working context.

    With no arguments, displays the current context.
    With content, replaces it.

    Examples:
        keep now                         # Show current context
        keep now "Working on auth flow"  # Set context
        keep now -f context.md           # Set from file
        keep now --reset                 # Reset to default
    """
    kp = _get_keeper(store, collection)

    # Determine if we're getting or setting
    setting = content is not None or file is not None or reset

    if setting:
        if reset:
            # Reset to default from system (delete first to clear old tags)
            from .api import _load_frontmatter, NOWDOC_ID, SYSTEM_DOC_DIR
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
        if tags:
            for tag in tags:
                if "=" not in tag:
                    typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
                    raise typer.Exit(1)
                k, v = tag.split("=", 1)
                parsed_tags[k] = v

        item = kp.set_now(new_content, tags=parsed_tags or None)
        typer.echo(_format_item(item, as_json=_get_json_output()))
    else:
        # Get current context
        item = kp.get_now()
        typer.echo(_format_item(item, as_json=_get_json_output()))


@app.command()
def get(
    id: Annotated[str, typer.Argument(help="URI of item to retrieve")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Retrieve a specific item by ID.
    """
    kp = _get_keeper(store, collection)
    item = kp.get(id)

    if item is None:
        typer.echo(f"Not found: {id}", err=True)
        raise typer.Exit(1)

    typer.echo(_format_item(item, as_json=_get_json_output()))


@app.command()
def exists(
    id: Annotated[str, typer.Argument(help="URI to check")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Check if an item exists in the store.
    """
    kp = _get_keeper(store, collection)
    found = kp.exists(id)
    
    if found:
        typer.echo(f"Exists: {id}")
    else:
        typer.echo(f"Not found: {id}")
        raise typer.Exit(1)


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


@app.command()
def init(
    store: StoreOption = None,
    collection: CollectionOption = "default",
):
    """
    Initialize or verify the store is ready.
    """
    kp = _get_keeper(store, collection)

    # Show config and store paths
    config = kp._config
    config_path = config.config_path if config else None
    store_path = kp._store_path

    # Show paths
    typer.echo(f"Config: {config_path}")
    if config and config.config_dir and config.config_dir.resolve() != store_path.resolve():
        typer.echo(f"Store:  {store_path}")

    typer.echo(f"Collections: {kp.list_collections()}")

    # Show detected providers
    if config:
        typer.echo(f"\nProviders:")
        typer.echo(f"  Embedding: {config.embedding.name}")
        typer.echo(f"  Summarization: {config.summarization.name}")



@app.command()
def config(
    store: StoreOption = None,
):
    """
    Show current configuration and store location.
    """
    kp = _get_keeper(store, "default")

    cfg = kp._config
    config_path = cfg.config_path if cfg else None
    store_path = kp._store_path

    if _get_json_output():
        result = {
            "store": str(store_path),
            "config": str(config_path) if config_path else None,
            "collections": kp.list_collections(),
        }
        if cfg:
            result["embedding"] = cfg.embedding.name
            result["summarization"] = cfg.summarization.name
        typer.echo(json.dumps(result, indent=2))
    else:
        # Show paths
        typer.echo(f"Config: {config_path}")
        if cfg and cfg.config_dir and cfg.config_dir.resolve() != store_path.resolve():
            typer.echo(f"Store:  {store_path}")

        typer.echo(f"Collections: {kp.list_collections()}")

        if cfg:
            typer.echo(f"\nProviders:")
            typer.echo(f"  Embedding: {cfg.embedding.name}")
            typer.echo(f"  Summarization: {cfg.summarization.name}")


@app.command("system")
def list_system(
    store: StoreOption = None,
):
    """
    List the system documents.

    Shows ID and summary for each. Use `keep get ID` for full details.
    """
    kp = _get_keeper(store, "default")
    docs = kp.list_system_documents()

    # Use --ids flag for pipe-friendly output
    if _get_ids_output():
        ids = [doc.id for doc in docs]
        if _get_json_output():
            typer.echo(json.dumps(ids))
        else:
            for doc_id in ids:
                typer.echo(doc_id)
        return

    if _get_json_output():
        typer.echo(json.dumps([
            {"id": doc.id, "summary": doc.summary}
            for doc in docs
        ], indent=2))
    else:
        if not docs:
            typer.echo("No system documents.")
        else:
            for doc in docs:
                # Compact summary: collapse whitespace, truncate to 70 chars
                summary = " ".join(doc.summary.split())[:70]
                if len(doc.summary) > 70:
                    summary += "..."
                typer.echo(f"{doc.id}: {summary}")


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

    # Daemon mode: write PID, process all, remove PID, exit silently
    if daemon:
        import signal

        pid_path = kp._processor_pid_path
        shutdown_requested = False

        def handle_signal(signum, frame):
            nonlocal shutdown_requested
            shutdown_requested = True

        # Handle common termination signals gracefully
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            # Write PID file
            pid_path.write_text(str(os.getpid()))

            # Process all items until queue empty or shutdown requested
            while not shutdown_requested:
                processed = kp.process_pending(limit=50)
                if processed == 0:
                    break

        finally:
            # Clean up PID file
            try:
                pid_path.unlink()
            except OSError:
                pass
            # Close resources
            kp.close()
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
        total_processed = 0
        while True:
            processed = kp.process_pending(limit=50)
            total_processed += processed
            if processed == 0:
                break
            if not _get_json_output():
                typer.echo(f"  Processed {total_processed}...")

        remaining = kp.pending_count()
        if _get_json_output():
            typer.echo(json.dumps({
                "processed": total_processed,
                "remaining": remaining
            }))
        else:
            typer.echo(f"✓ Processed {total_processed} items, {remaining} remaining")
    else:
        # Process limited batch
        processed = kp.process_pending(limit=limit)
        remaining = kp.pending_count()

        if _get_json_output():
            typer.echo(json.dumps({
                "processed": processed,
                "remaining": remaining
            }))
        else:
            typer.echo(f"✓ Processed {processed} items, {remaining} remaining")


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
