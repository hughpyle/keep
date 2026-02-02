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


app = typer.Typer(
    name="keep",
    help="Associative memory with semantic search.",
    no_args_is_help=True,
)


@app.callback()
def main_callback(
    verbose: Annotated[bool, typer.Option(
        "--verbose", "-v",
        help="Enable debug-level logging to stderr",
        callback=_verbose_callback,
        is_eager=True,
    )] = False,
):
    """Associative memory with semantic search."""
    pass


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

JsonOption = Annotated[
    bool,
    typer.Option(
        "--json", "-j",
        help="Output as JSON"
    )
]


# -----------------------------------------------------------------------------
# Output Helpers
# -----------------------------------------------------------------------------

def _format_item(item: Item, as_json: bool = False) -> str:
    if as_json:
        return json.dumps({
            "id": item.id,
            "summary": item.summary,
            "tags": item.tags,
            "score": item.score,
        })
    else:
        score = f"[{item.score:.3f}] " if item.score is not None else ""
        return f"{score}{item.id}\n  {item.summary}"


def _format_items(items: list[Item], as_json: bool = False) -> str:
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


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

@app.command()
def find(
    query: Annotated[str, typer.Argument(help="Search query text")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    output_json: JsonOption = False,
):
    """
    Find items using semantic similarity search.
    """
    kp = _get_keeper(store, collection)
    results = kp.find(query, limit=limit)
    typer.echo(_format_items(results, as_json=output_json))


@app.command()
def similar(
    id: Annotated[str, typer.Argument(help="URI of item to find similar items for")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    include_self: Annotated[bool, typer.Option(help="Include the queried item")] = False,
    output_json: JsonOption = False,
):
    """
    Find items similar to an existing item.
    """
    kp = _get_keeper(store, collection)
    results = kp.find_similar(id, limit=limit, include_self=include_self)
    typer.echo(_format_items(results, as_json=output_json))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Full-text search query")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 10,
    output_json: JsonOption = False,
):
    """
    Search item summaries using full-text search.
    """
    kp = _get_keeper(store, collection)
    results = kp.query_fulltext(query, limit=limit)
    typer.echo(_format_items(results, as_json=output_json))


@app.command()
def tag(
    key: Annotated[Optional[str], typer.Argument(help="Tag key to search for")] = None,
    value: Annotated[Optional[str], typer.Argument(help="Tag value (optional)")] = None,
    list_tags: Annotated[bool, typer.Option(
        "--list", "-l",
        help="List distinct tag keys, or values if key is provided"
    )] = False,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 100,
    output_json: JsonOption = False,
):
    """
    Find items by tag or list available tags.

    Examples:
        keep tag --list              # List all tag keys
        keep tag project             # Find docs with 'project' tag (any value)
        keep tag project myapp       # Find docs with project=myapp
        keep tag project --list      # List distinct values for 'project'
    """
    kp = _get_keeper(store, collection)

    # List mode
    if list_tags:
        tags = kp.list_tags(key, collection=collection)
        if output_json:
            typer.echo(json.dumps(tags))
        else:
            if not tags:
                typer.echo("No tags found.")
            else:
                for t in tags:
                    typer.echo(t)
        return

    # Query mode - key is required
    if key is None:
        typer.echo("Error: Specify a tag key or use --list", err=True)
        raise typer.Exit(1)

    results = kp.query_tag(key, value, limit=limit)
    typer.echo(_format_items(results, as_json=output_json))


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
    output_json: JsonOption = False,
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

    if output_json:
        typer.echo(_format_items(results, as_json=True))
    else:
        for item in results:
            typer.echo(_format_item(item, as_json=False))


@app.command()
def update(
    id: Annotated[str, typer.Argument(help="URI of document to index")],
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
        "--lazy", "-l",
        help="Fast mode: use truncated summary, queue for later processing"
    )] = False,
    output_json: JsonOption = False,
):
    """
    Add or update a document in the store.

    Use --summary to provide your own summary (skips auto-summarization).
    Use --lazy for fast indexing when summarization is slow.
    Run 'keep process-pending' later to generate real summaries.
    """
    kp = _get_keeper(store, collection)

    # Parse tags from key=value format
    parsed_tags = {}
    if tags:
        for tag in tags:
            if "=" not in tag:
                typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
                raise typer.Exit(1)
            k, v = tag.split("=", 1)
            parsed_tags[k] = v

    item = kp.update(id, tags=parsed_tags or None, summary=summary, lazy=lazy)
    typer.echo(_format_item(item, as_json=output_json))


@app.command()
def remember(
    content: Annotated[str, typer.Argument(help="Content to remember")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    id: Annotated[Optional[str], typer.Option(
        "--id", "-i",
        help="Custom identifier (default: auto-generated)"
    )] = None,
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Tag as key=value (can be repeated)"
    )] = None,
    summary: Annotated[Optional[str], typer.Option(
        "--summary",
        help="User-provided summary (skips auto-summarization)"
    )] = None,
    lazy: Annotated[bool, typer.Option(
        "--lazy", "-l",
        help="Fast mode: use truncated summary, queue for later processing"
    )] = False,
    output_json: JsonOption = False,
):
    """
    Remember inline content (conversations, notes, insights).

    Short content (≤500 chars) is used verbatim as its own summary.
    Use --summary to provide your own summary for longer content.
    Use --lazy for fast indexing when summarization is slow.
    """
    kp = _get_keeper(store, collection)

    # Parse tags from key=value format
    parsed_tags = {}
    if tags:
        for tag in tags:
            if "=" not in tag:
                typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
                raise typer.Exit(1)
            k, v = tag.split("=", 1)
            parsed_tags[k] = v

    item = kp.remember(content, id=id, summary=summary, tags=parsed_tags or None, lazy=lazy)
    typer.echo(_format_item(item, as_json=output_json))


@app.command()
def get(
    id: Annotated[str, typer.Argument(help="URI of item to retrieve")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    output_json: JsonOption = False,
):
    """
    Retrieve a specific item by ID.
    """
    kp = _get_keeper(store, collection)
    item = kp.get(id)
    
    if item is None:
        typer.echo(f"Not found: {id}", err=True)
        raise typer.Exit(1)
    
    typer.echo(_format_item(item, as_json=output_json))


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
    output_json: JsonOption = False,
):
    """
    List all collections in the store.
    """
    kp = _get_keeper(store, "default")
    collections = kp.list_collections()
    
    if output_json:
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

    # Show paths (config and store may differ)
    if config and config.config_dir and config.config_dir.resolve() != store_path.resolve():
        typer.echo(f"Config: {config_path}")
        typer.echo(f"Store:  {store_path}")
    else:
        typer.echo(f"Store: {store_path}")

    typer.echo(f"Collections: {kp.list_collections()}")

    # Show detected providers
    try:
        if config:
            typer.echo(f"\nProviders:")
            typer.echo(f"  Embedding: {config.embedding.name}")
            typer.echo(f"  Summarization: {config.summarization.name}")
            typer.echo(f"\nTo customize, edit {config_path}")
    except Exception:
        pass  # Don't fail if provider detection doesn't work

    # .gitignore reminder
    typer.echo(f"\nRemember to add .keep/ to .gitignore")


@app.command("system")
def list_system(
    store: StoreOption = None,
    output_json: JsonOption = False,
):
    """
    List the system documents.
    """
    kp = _get_keeper(store, "default")
    docs = kp.list_system_documents()
    typer.echo(_format_items(docs, as_json=output_json))


@app.command("routing")
def show_routing(
    store: StoreOption = None,
    output_json: JsonOption = False,
):
    """
    Show the current routing configuration.
    """
    kp = _get_keeper(store, "default")
    routing = kp.get_routing()

    if output_json:
        from dataclasses import asdict
        typer.echo(json.dumps(asdict(routing), indent=2))
    else:
        typer.echo(f"Summary: {routing.summary}")
        typer.echo(f"Private patterns: {routing.private_patterns}")
        typer.echo(f"Updated: {routing.updated}")


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
    output_json: JsonOption = False,
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
        if output_json:
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
            if not output_json:
                typer.echo(f"  Processed {total_processed}...")

        remaining = kp.pending_count()
        if output_json:
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

        if output_json:
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
    app()


if __name__ == "__main__":
    main()
