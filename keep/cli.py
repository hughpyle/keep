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

from .api import AssociativeMemory
from .types import Item
from .logging_config import configure_quiet_mode


# Configure quiet mode by default (suppress verbose library output)
# Set KEEP_VERBOSE=1 to enable verbose mode for debugging
_verbose = sys.argv and "--verbose" in sys.argv or os.environ.get("KEEP_VERBOSE") == "1"
configure_quiet_mode(quiet=not _verbose)


app = typer.Typer(
    name="keep",
    help="Associative memory with semantic search.",
    no_args_is_help=True,
)


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


def _get_memory(store: Optional[Path], collection: str) -> AssociativeMemory:
    """Initialize memory, handling errors gracefully."""
    # store=None is fine — AssociativeMemory will use default (git root/.keep)
    try:
        return AssociativeMemory(store, collection=collection)
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
    mem = _get_memory(store, collection)
    results = mem.find(query, limit=limit)
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
    mem = _get_memory(store, collection)
    results = mem.find_similar(id, limit=limit, include_self=include_self)
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
    mem = _get_memory(store, collection)
    results = mem.query_fulltext(query, limit=limit)
    typer.echo(_format_items(results, as_json=output_json))


@app.command()
def tag(
    key: Annotated[str, typer.Argument(help="Tag key to search for")],
    value: Annotated[Optional[str], typer.Argument(help="Tag value (optional)")] = None,
    store: StoreOption = None,
    collection: CollectionOption = "default",
    limit: LimitOption = 100,
    output_json: JsonOption = False,
):
    """
    Find items by tag.
    """
    mem = _get_memory(store, collection)
    results = mem.query_tag(key, value, limit=limit)
    typer.echo(_format_items(results, as_json=output_json))


@app.command()
def update(
    id: Annotated[str, typer.Argument(help="URI of document to index")],
    store: StoreOption = None,
    collection: CollectionOption = "default",
    tags: Annotated[Optional[list[str]], typer.Option(
        "--tag", "-t",
        help="Source tag as key=value (can be repeated)"
    )] = None,
    output_json: JsonOption = False,
):
    """
    Add or update a document in the store.
    """
    mem = _get_memory(store, collection)
    
    # Parse tags from key=value format
    source_tags = {}
    if tags:
        for tag in tags:
            if "=" not in tag:
                typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
                raise typer.Exit(1)
            k, v = tag.split("=", 1)
            source_tags[k] = v
    
    item = mem.update(id, source_tags=source_tags or None)
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
        help="Source tag as key=value (can be repeated)"
    )] = None,
    output_json: JsonOption = False,
):
    """
    Remember inline content (conversations, notes, insights).
    """
    mem = _get_memory(store, collection)
    
    # Parse tags from key=value format
    source_tags = {}
    if tags:
        for tag in tags:
            if "=" not in tag:
                typer.echo(f"Error: Invalid tag format '{tag}'. Use key=value", err=True)
                raise typer.Exit(1)
            k, v = tag.split("=", 1)
            source_tags[k] = v
    
    item = mem.remember(content, id=id, source_tags=source_tags or None)
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
    mem = _get_memory(store, collection)
    item = mem.get(id)
    
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
    mem = _get_memory(store, collection)
    found = mem.exists(id)
    
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
    mem = _get_memory(store, "default")
    collections = mem.list_collections()
    
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
    mem = _get_memory(store, collection)
    
    # Show actual store path
    actual_path = mem._store_path if hasattr(mem, '_store_path') else Path(store or ".keep")
    typer.echo(f"✓ Store ready: {actual_path}")
    typer.echo(f"✓ Collections: {mem.list_collections()}")
    
    # Show detected providers
    try:
        if hasattr(mem, '_config'):
            config = mem._config
            typer.echo(f"\n✓ Detected providers:")
            typer.echo(f"  Embedding: {config.embedding.name}")
            typer.echo(f"  Summarization: {config.summarization.name}")
            typer.echo(f"\nTo customize, edit {actual_path}/keep.toml")
    except Exception:
        pass  # Don't fail if provider detection doesn't work
    
    # .gitignore reminder
    typer.echo(f"\n⚠️  Remember to add .keep/ to .gitignore")


@app.command("system")
def list_system(
    store: StoreOption = None,
    output_json: JsonOption = False,
):
    """
    List all system documents (schema as data).
    """
    mem = _get_memory(store, "default")
    docs = mem.list_system_documents()
    typer.echo(_format_items(docs, as_json=output_json))


@app.command("routing")
def show_routing(
    store: StoreOption = None,
    output_json: JsonOption = False,
):
    """
    Show the current routing configuration.
    """
    mem = _get_memory(store, "default")
    routing = mem.get_routing()
    
    if output_json:
        from dataclasses import asdict
        typer.echo(json.dumps(asdict(routing), indent=2))
    else:
        typer.echo(f"Summary: {routing.summary}")
        typer.echo(f"Private patterns: {routing.private_patterns}")
        typer.echo(f"Updated: {routing.updated}")


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main():
    app()


if __name__ == "__main__":
    main()
