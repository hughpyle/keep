"""Thin CLI — pure display layer over the daemon HTTP API.

Parse args → one HTTP call → render JSON → exit.
No keep internals, no models, no database. ~50ms startup.
"""

import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote

import typer

app = typer.Typer(
    name="keep",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
    rich_markup_mode=None,
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _q(id: str) -> str:
    """URL-encode an ID for path segments."""
    return quote(id, safe="")


def _http(method: str, port: int, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Make an HTTP request to the daemon. Returns (status, json_body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {}
    data = None
    if body is not None:
        data = json.dumps({k: v for k, v in body.items() if v is not None})
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    conn.request(method, path, data, headers)
    resp = conn.getresponse()
    result = json.loads(resp.read())
    status = resp.status
    conn.close()
    return status, result


def _get(port: int, path: str) -> dict:
    status, body = _http("GET", port, path)
    if status == 404:
        typer.echo(f"Not found", err=True)
        raise typer.Exit(1)
    if status != 200:
        typer.echo(f"Error: {body.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)
    return body


def _post(port: int, path: str, body: dict) -> dict:
    status, result = _http("POST", port, path, body)
    if status != 200:
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)
    return result


def _patch(port: int, path: str, body: dict) -> dict:
    status, result = _http("PATCH", port, path, body)
    if status != 200:
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)
    return result


def _delete(port: int, path: str) -> dict:
    status, result = _http("DELETE", port, path)
    if status != 200:
        typer.echo(f"Error: {result.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)
    return result


# ---------------------------------------------------------------------------
# Daemon port resolution + auto-start
# ---------------------------------------------------------------------------

def _resolve_store_path() -> Path:
    """Resolve store path from env or config, without importing keep internals."""
    override = os.environ.get("KEEP_STORE_PATH")
    if override:
        return Path(override).resolve()
    config_dir = Path(os.environ.get("KEEP_CONFIG", "")) if os.environ.get("KEEP_CONFIG") else Path.home() / ".keep"
    config_file = config_dir / "keep.toml"
    if config_file.exists():
        try:
            import tomllib
            with open(config_file, "rb") as f:
                data = tomllib.load(f)
            val = data.get("store", {}).get("path")
            if val:
                return Path(val).expanduser().resolve()
        except Exception:
            pass
    # Default: config dir IS the store (matches get_default_store_path behavior)
    return config_dir.resolve()


def _check_health(port: int) -> bool:
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        conn.request("GET", "/v1/health")
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status == 200
    except Exception:
        return False


def _get_health(port: int) -> dict | None:
    """Get full health response from daemon."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/v1/health")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return data if resp.status == 200 else None
    except Exception:
        return None


def _check_setup(port: int) -> None:
    """Check daemon health for setup issues. Prints warnings, exits if setup needed."""
    health = _get_health(port)
    if health is None:
        return
    if health.get("needs_setup"):
        typer.echo("keep is not configured. Run: keep config --setup", err=True)
        raise typer.Exit(1)
    for warning in health.get("warnings", []):
        typer.echo(f"Warning: {warning}", err=True)


def _start_daemon(store_path: Path) -> None:
    """Spawn daemon process."""
    cmd = [sys.executable, "-m", "keep.cli", "pending", "--daemon", "--store", str(store_path)]
    log_path = store_path / "keep-ops.log"
    store_path.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log_fd:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": log_fd, "stdin": subprocess.DEVNULL}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(cmd, **kwargs)


def _get_port() -> int:
    """Get daemon port, auto-starting if needed."""
    store_path = _resolve_store_path()
    port_file = store_path / ".daemon.port"

    # Try existing daemon
    if port_file.exists():
        try:
            port = int(port_file.read_text().strip())
            if _check_health(port):
                _check_setup(port)
                return port
        except (ValueError, OSError):
            pass

    # Auto-start daemon
    typer.echo("Starting daemon...", err=True)
    _start_daemon(store_path)

    # Poll for readiness
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if port_file.exists():
            try:
                port = int(port_file.read_text().strip())
                if _check_health(port):
                    _check_setup(port)
                    return port
            except (ValueError, OSError):
                pass
        time.sleep(0.3)

    typer.echo("Error: daemon did not start in time.", err=True)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _width() -> int:
    return shutil.get_terminal_size((200, 24)).columns


def _truncate(text: str, max_len: int) -> str:
    """Replace newlines with spaces and truncate at word boundary."""
    text = text.replace("\n", " ")
    if len(text) > max_len:
        return text[:max_len - 3].rsplit(" ", 1)[0] + "..."
    return text


def _date(tags: dict) -> str:
    """Extract display date from tags."""
    from keep.types import local_date
    for key in ("_updated", "_created"):
        val = tags.get(key, "")
        if val:
            return local_date(val)
    return ""


def _display_tags(tags: dict) -> dict:
    """Filter to user-visible tags (matches keep/types.py:INTERNAL_TAGS)."""
    from keep.types import INTERNAL_TAGS
    return {k: v for k, v in tags.items()
            if k not in INTERNAL_TAGS
            and not k.startswith("_tk::")
            and not k.startswith("_tv::")}


def _yaml_quote(v: str) -> str:
    """Quote a YAML scalar value (matches cli.py's _quote_scalar_tag_value)."""
    return json.dumps(str(v))


def _render_tags_block(tags: dict) -> str:
    """Render tags as YAML-style indented block matching old CLI output."""
    display = _display_tags(tags)
    if not display:
        return ""
    lines = []
    for k, v in sorted(display.items()):
        if isinstance(v, list):
            lines.append(f"  {k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"  {k}: {_yaml_quote(str(v))}")
    return "\n".join(lines)


def _render_item_line(item: dict, w: int, id_width: int = 0) -> str:
    """Format a single item as a summary line."""
    id = item.get("id", "")
    score = item.get("score")
    tags = item.get("tags", {})
    date = _date(tags)
    padded = id.ljust(id_width) if id_width else id
    score_str = f" ({score:.2f})" if score is not None else ""
    date_str = f" {date}" if date else ""
    prefix_len = len(padded) + len(score_str) + len(date_str) + 2
    summary = _truncate(item.get("summary", ""), w - prefix_len)
    return f"{padded}{score_str}{date_str} {summary}"


def _render_context(data: dict) -> str:
    """Render ItemContext JSON as YAML frontmatter."""
    item = data.get("item", {})
    w = _width()
    lines = ["---"]

    # ID + version
    id_str = item.get("id", "")
    offset = data.get("viewing_offset", 0)
    if offset > 0:
        id_str += f"@V{{{offset}}}"
    lines.append(f"id: {id_str}")

    # Tags
    tags_block = _render_tags_block(item.get("tags", {}))
    if tags_block:
        lines.append("tags:")
        lines.append(tags_block)

    # Similar
    similar = data.get("similar", [])
    if similar:
        sim_ids = [s.get("id", "") for s in similar]
        id_width = min(max(len(s) for s in sim_ids), 20) if sim_ids else 0
        lines.append("similar:")
        for s in similar:
            sid = s.get("id", "")
            score = s.get("score")
            date = s.get("date", "")
            padded = sid.ljust(id_width)
            score_str = f"({score:.2f})" if score is not None else ""
            summary = _truncate(s.get("summary", ""), w - id_width - 25)
            lines.append(f"  - {padded} {score_str} {date} {summary}")

    # Meta sections
    meta = data.get("meta", {})
    for section, items in sorted(meta.items()):
        if items:
            meta_ids = [m.get("id", "") for m in items]
            id_width = min(max(len(s) for s in meta_ids), 20) if meta_ids else 0
            lines.append(f"meta/{section}:")
            for m in items:
                mid = m.get("id", "")
                padded = mid.ljust(id_width)
                msummary = _truncate(m.get("summary", ""), w - id_width - 10)
                lines.append(f"  - {padded} {msummary}")

    # Edges
    edges = data.get("edges", {})
    for pred, refs in sorted(edges.items()):
        if refs:
            lines.append(f"edges/{pred}:")
            for e in refs:
                eid = e.get("source_id", "")
                edate = e.get("date", "")
                esummary = _truncate(e.get("summary", ""), w - len(eid) - 15)
                lines.append(f"  - {eid} {edate} {esummary}")

    # Parts (compact: first + "N more" + last when many)
    parts = data.get("parts", [])
    if parts:
        lines.append("parts:")
        if len(parts) <= 3:
            for p in parts:
                pnum = p.get("part_num", 0)
                psummary = _truncate(p.get("summary", ""), w - 15)
                lines.append(f"  - @P{{{pnum}}} {psummary}")
        else:
            first, last = parts[0], parts[-1]
            lines.append(f"  - @P{{{first.get('part_num', 0)}}} {_truncate(first.get('summary', ''), w - 15)}")
            lines.append(f"  # (...{len(parts) - 2} more...)")
            lines.append(f"  - @P{{{last.get('part_num', 0)}}} {_truncate(last.get('summary', ''), w - 15)}")

    # Version navigation
    prev = data.get("prev", [])
    if prev:
        lines.append("prev:")
        for v in prev:
            voff = v.get("offset", 0)
            vdate = v.get("date", "")
            vsummary = _truncate(v.get("summary", ""), w - 20)
            lines.append(f"  - @V{{{voff}}} {vdate} {vsummary}")

    nxt = data.get("next", [])
    if nxt:
        lines.append("next:")
        for v in nxt:
            voff = v.get("offset", 0)
            vdate = v.get("date", "")
            vsummary = _truncate(v.get("summary", ""), w - 20)
            lines.append(f"  - @V{{{voff}}} {vdate} {vsummary}")

    lines.append("---")

    # Summary body
    summary = item.get("summary", "")
    if summary:
        lines.append(summary)

    return "\n".join(lines)


def _render_find(data: dict) -> str:
    """Render search results."""
    w = _width()
    notes = data.get("notes", [])
    deep_groups = {g["id"]: g["items"] for g in data.get("deep_groups", []) if g.get("id")}

    # Compute aligned ID width
    all_ids = [n.get("id", "") for n in notes]
    id_width = min(max((len(i) for i in all_ids), default=0), 20)

    lines = []
    for item in notes:
        lines.append(_render_item_line(item, w, id_width))
        # Deep group items
        item_id = item.get("id", "")
        base_id = item_id.split("@")[0] if "@" in item_id else item_id
        group = deep_groups.get(base_id, deep_groups.get(item_id, []))
        for deep in group:
            deep_line = _render_item_line(deep, w - 2)
            lines.append(f"  {deep_line}")

    if not lines:
        return "No results."
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

StoreOption = Annotated[Optional[str], typer.Option("--store", "-s", envvar="KEEP_STORE_PATH", help="Store path")]
JsonFlag = Annotated[bool, typer.Option("--json", "-j", help="JSON output")]
LimitOption = Annotated[int, typer.Option("--limit", "-l", help="Result limit")]


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context, store: StoreOption = None, json_output: JsonFlag = False):
    """keep — reflective memory for AI agents."""
    if ctx.invoked_subcommand is None:
        # No subcommand → show "now"
        port = _get_port()
        data = _get(port, f"/v1/notes/{_q('now')}/context")
        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(_render_context(data))


@app.command()
def get(
    id: Annotated[list[str], typer.Argument(help="Item ID(s)")],
    version: Annotated[Optional[int], typer.Option("-V", "--version", help="Version offset")] = None,
    limit: LimitOption = 10,
    json_output: JsonFlag = False,
):
    """Retrieve note(s) by ID.

    \b
    Accepts one or more IDs. Version: append @V{N}. Part: append @P{N}.
    Examples:
        keep get doc:1                  # Current version
        keep get doc:1 doc:2 doc:3      # Multiple notes
        keep get doc:1 -V 1             # Previous version
        keep get "doc:1@P{1}"           # Part 1
    """
    port = _get_port()
    outputs = []
    errors = 0
    for item_id in id:
        params = f"?similar_limit={limit}&meta_limit={limit}&edges_limit={limit}"
        if version is not None:
            params += f"&version={version}"
        status, data = _http("GET", port, f"/v1/notes/{_q(item_id)}/context{params}")
        if status == 404:
            typer.echo(f"Not found: {item_id}", err=True)
            errors += 1
            continue
        if status != 200:
            typer.echo(f"Error: {data.get('error', 'unknown')}", err=True)
            errors += 1
            continue
        if json_output:
            outputs.append(json.dumps(data, indent=2))
        else:
            outputs.append(_render_context(data))
    if outputs:
        typer.echo("\n---\n".join(outputs) if len(outputs) > 1 else outputs[0])
    if errors and not outputs:
        raise typer.Exit(1)


@app.command()
def find(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: LimitOption = 10,
    similar: Annotated[Optional[str], typer.Option("--similar", help="Find similar to ID")] = None,
    deep: Annotated[bool, typer.Option("--deep", "-d", help="Deep edge follow")] = False,
    since: Annotated[Optional[str], typer.Option("--since", help="Updated since")] = None,
    until: Annotated[Optional[str], typer.Option("--until", help="Updated before")] = None,
    scope: Annotated[Optional[str], typer.Option("--scope", help="ID glob scope")] = None,
    json_output: JsonFlag = False,
):
    """Find notes by hybrid search (semantic + full-text) or similarity.

    \b
    Examples:
        keep find "auth patterns"           # Semantic + full-text search
        keep find --similar %abc123         # Find similar to item
        keep find "auth" --deep             # Follow tags for related items
        keep find "auth" --since P7D        # Last 7 days only
    """
    port = _get_port()
    body = {
        "query": query if not similar else None,
        "similar_to": similar,
        "limit": limit,
        "deep": deep or None,
        "since": since,
        "until": until,
        "scope": scope,
    }
    data = _post(port, "/v1/search", body)
    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_render_find(data))


@app.command()
def put(
    source: Annotated[Optional[str], typer.Argument(help="Content, file path, URI, or '-' for stdin")] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i", help="Item ID")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Tags (key=value)")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", help="Explicit summary")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force update")] = False,
    json_output: JsonFlag = False,
):
    """Add or update a note in the store.

    \b
    Input modes (auto-detected):
      keep put /path/to/file.pdf     # File mode
      keep put https://example.com   # URI mode
      keep put /path/to/folder/      # Directory mode
      keep put "my note"             # Text mode (content-addressed ID)
      keep put -                     # Stdin mode
    """
    port = _get_port()
    parsed_tags = {}
    for t in (tags or []):
        if "=" not in t:
            typer.echo(f"Invalid tag format: {t!r} (expected key=value)", err=True)
            raise typer.Exit(1)
        k, v = t.split("=", 1)
        parsed_tags[k] = v

    # Stdin mode
    if source == "-" or (source is None and not sys.stdin.isatty()):
        content = sys.stdin.read()
        if summary is not None:
            typer.echo("Error: --summary cannot be used with stdin (original content would be lost)", err=True)
            raise typer.Exit(1)
        body = {"content": content, "id": id, "tags": parsed_tags or None, "force": force or None}
        data = _post(port, "/v1/notes", body)
    elif source is None:
        typer.echo("Error: provide content, URI, or '-' for stdin", err=True)
        raise typer.Exit(1)
    else:
        # Detect file/URL/directory
        content = source
        uri = None
        if source.startswith(("file://", "http://", "https://")):
            uri = source
            content = None
        elif Path(source).is_dir():
            if summary is not None:
                typer.echo("Error: --summary cannot be used with directory mode", err=True)
                raise typer.Exit(1)
            if id is not None:
                typer.echo("Error: --id cannot be used with directory mode", err=True)
                raise typer.Exit(1)
            # Directory mode — delegate to full CLI for recursion support
            from keep.cli import app as full_app
            args = ["put", source]
            for t in (tags or []):
                args += ["-t", t]
            if force:
                args += ["--force"]
            try:
                full_app(args, standalone_mode=False)
            except SystemExit as e:
                raise typer.Exit(e.code or 0)
            return
        elif Path(source).exists() and not source.startswith("%"):
            uri = f"file://{Path(source).resolve()}"
            content = None

        # Inline text + --summary rejected (original content would be lost)
        if content is not None and uri is None and summary is not None:
            typer.echo("Error: --summary cannot be used with inline text (original content would be lost)", err=True)
            typer.echo("Hint: write to a file first, then: keep put file:///path/to/file --summary '...'", err=True)
            raise typer.Exit(1)

        body = {
            "content": content,
            "uri": uri,
            "id": id,
            "tags": parsed_tags or None,
            "summary": summary,
            "force": force or None,
        }
        data = _post(port, "/v1/notes", body)

    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(f"{data.get('id', '')} stored.")


# Hidden aliases for put
@app.command("update", hidden=True)
def update_alias(
    source: Annotated[Optional[str], typer.Argument()] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
    json_output: JsonFlag = False,
):
    """Alias for 'put'."""
    put(source=source, id=id, tags=tags, summary=summary, force=force, json_output=json_output)


@app.command("add", hidden=True)
def add_alias(
    source: Annotated[Optional[str], typer.Argument()] = None,
    id: Annotated[Optional[str], typer.Option("--id", "-i")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary")] = None,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
    json_output: JsonFlag = False,
):
    """Alias for 'put'."""
    put(source=source, id=id, tags=tags, summary=summary, force=force, json_output=json_output)


@app.command()
def tag(
    id: Annotated[str, typer.Argument(help="Item ID")],
    tags: Annotated[list[str], typer.Argument(help="Tags (key=value or key= to remove)")],
    json_output: JsonFlag = False,
):
    """Add, update, or remove tags on existing notes.

    \b
    Does not re-process the note — only updates tags.
    Use key=value to set, key= (empty value) to remove.
    """
    port = _get_port()
    set_tags = {}
    remove = []
    for t in tags:
        if "=" in t:
            k, v = t.split("=", 1)
            if v:
                set_tags[k] = v
            else:
                remove.append(k)
        else:
            set_tags[t] = "true"
    data = _patch(port, f"/v1/notes/{_q(id)}/tags", {"set": set_tags, "remove": remove})
    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(f"{data.get('id', '')} tagged.")


@app.command("tag-update", hidden=True)
def tag_update_alias(
    id: Annotated[str, typer.Argument(help="Item ID")],
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag")] = None,
    json_output: JsonFlag = False,
):
    """Alias for 'tag'."""
    tag(id=id, tags=[t for t in (tags or [])], json_output=json_output)


@app.command("del")
def delete_cmd(
    id: Annotated[list[str], typer.Argument(help="Item ID(s)")],
):
    """Delete the current version of note(s), or a specific version."""
    port = _get_port()
    for item_id in id:
        data = _delete(port, f"/v1/notes/{_q(item_id)}")
        if data.get("deleted"):
            typer.echo(f"{item_id} deleted.")
        else:
            typer.echo(f"{item_id} not found.", err=True)


@app.command("delete", hidden=True)
def delete_alias(id: Annotated[list[str], typer.Argument(help="Item ID(s)")]):
    """Alias for 'del'."""
    delete_cmd(id=id)


@app.command()
def now(
    content: Annotated[Optional[str], typer.Argument(help="New content")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Tags")] = None,
    json_output: JsonFlag = False,
):
    """Get or set the current working intentions.

    \b
    With no arguments, displays the current intentions.
    With content, replaces them (previous version is preserved).
    """
    port = _get_port()
    if content:
        parsed_tags = {}
        for t in (tags or []):
            if "=" in t:
                k, v = t.split("=", 1)
                parsed_tags[k] = v
        _post(port, "/v1/notes", {"content": content, "id": "now", "tags": parsed_tags or None})
    data = _get(port, f"/v1/notes/{_q('now')}/context")
    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_render_context(data))


@app.command("list")
def list_cmd(
    prefix: Annotated[Optional[str], typer.Argument(help="ID prefix or glob filter")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Filter by tag")] = None,
    limit: LimitOption = 20,
    since: Annotated[Optional[str], typer.Option("--since")] = None,
    until: Annotated[Optional[str], typer.Option("--until")] = None,
    json_output: JsonFlag = False,
):
    """List recent notes, filter by tags, or list tag keys/values.

    \b
    Examples:
        keep list                      # Recent notes
        keep list .tag                 # All .tag/* system docs
        keep list -t project=myapp     # Filter by tag
        keep list --since P7D          # Last 7 days
    """
    port = _get_port()
    tag_filter = {}
    for t in (tags or []):
        if "=" in t:
            k, v = t.split("=", 1)
            tag_filter[k] = v
    if not prefix:
        # No query — list recent items via flow
        data = _post(port, "/v1/flow", {
            "state": "list_items",
            "params": {"tags": tag_filter or None, "limit": limit, "since": since, "until": until},
        })
        # Flow returns items in bindings; fall back to search if flow unavailable
        items = data.get("bindings", {}).get("results", {}).get("results", [])
        if items:
            data = {"notes": items}
        else:
            # Fallback: search with a broad query
            data = _post(port, "/v1/search", {
                "query": " ", "tags": tag_filter or None, "limit": limit,
                "since": since, "until": until,
            })
    else:
        data = _post(port, "/v1/search", {
            "query": " ", "tags": tag_filter or None, "limit": limit,
            "since": since, "until": until, "scope": prefix,
            "include_hidden": prefix.startswith(".") if prefix else None,
        })
    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_render_find(data))


@app.command()
def move(
    name: Annotated[str, typer.Argument(help="Target collection name")],
    source: Annotated[str, typer.Option("--source", help="Source item ID")] = "now",
    json_output: JsonFlag = False,
):
    """Move versions from now (or another item) into a named item."""
    port = _get_port()
    data = _post(port, "/v1/flow", {
        "state": "move", "params": {"name": name, "source": source},
    })
    typer.echo(f"Moved to {name}.")


@app.command()
def prompt(
    name: Annotated[str, typer.Argument(help="Prompt name (e.g. 'reflect')")] = "",
    text: Annotated[Optional[str], typer.Argument(help="Text for context search")] = None,
    list_prompts: Annotated[bool, typer.Option("--list", "-l", help="List available prompts")] = False,
    id: Annotated[Optional[str], typer.Option("--id", help="Item ID for {get} context")] = None,
    tag: Annotated[Optional[list[str]], typer.Option("--tag", "-t", help="Filter by tag (key=value)")] = None,
    since: Annotated[Optional[str], typer.Option("--since", help="Updated since")] = None,
    until: Annotated[Optional[str], typer.Option("--until", help="Updated before")] = None,
    deep: Annotated[bool, typer.Option("--deep", "-D", help="Follow tags to discover related items")] = False,
    scope: Annotated[Optional[str], typer.Option("--scope", "-S", help="ID glob scope")] = None,
    token_budget: Annotated[Optional[int], typer.Option("--tokens", help="Token budget for {find}")] = None,
    json_output: JsonFlag = False,
):
    """Render an agent prompt with injected context.

    \b
    The prompt doc may contain {get} and {find} placeholders:
      {get}  — expanded with context for --id (default: now)
      {find} — expanded with search results for the text argument

    \b
    Examples:
        keep prompt --list                        # List available prompts
        keep prompt reflect                       # Reflect on current work
        keep prompt reflect "auth flow"           # With search context
        keep prompt reflect --since P7D           # Recent context only
    """
    port = _get_port()

    if list_prompts or not name:
        # List prompts via flow
        data = _post(port, "/v1/flow", {"state": "prompt", "params": {"list": True}})
        prompts = data.get("data", {}).get("prompts", [])
        if not prompts:
            typer.echo("No agent prompts available.", err=True)
            raise typer.Exit(1)
        if json_output:
            typer.echo(json.dumps({"prompts": prompts}, indent=2))
        else:
            for p in prompts:
                typer.echo(f"{p['name']:20s} {p.get('summary', '')}")
        return

    tags_dict = {}
    for t in (tag or []):
        if "=" in t:
            k, v = t.split("=", 1)
            tags_dict[k] = v

    # Render prompt via flow
    data = _post(port, "/v1/flow", {"state": "prompt", "params": {
        "name": name,
        "text": text,
        "id": id,
        "tags": tags_dict or None,
        "since": since,
        "until": until,
        "deep": deep or None,
        "scope": scope,
        "token_budget": token_budget,
    }})
    flow_data = data.get("data", {})
    if data.get("status") == "error":
        typer.echo(f"Error: {flow_data.get('error', 'unknown')}", err=True)
        raise typer.Exit(1)
    if json_output:
        typer.echo(json.dumps(flow_data, indent=2))
    else:
        typer.echo(flow_data.get("text", ""))


@app.command(hidden=True)
def reflect(
    text: Annotated[Optional[str], typer.Argument(help="Text for context search")] = None,
    id: Annotated[Optional[str], typer.Option("--id", help="Item ID for {get} context")] = None,
    json_output: JsonFlag = False,
):
    """Reflect on current actions (alias for 'keep prompt reflect')."""
    prompt(name="reflect", text=text, id=id, json_output=json_output)


@app.command("flow")
def flow_cmd(
    state: Annotated[Optional[str], typer.Argument(help="State doc name")] = None,
    target: Annotated[Optional[str], typer.Option("--target", "-t", help="Target note ID")] = None,
    file: Annotated[Optional[str], typer.Option("--file", "-f", help="YAML state doc file or '-' for stdin")] = None,
    budget: Annotated[Optional[int], typer.Option("--budget", "-b", help="Max ticks")] = None,
    cursor: Annotated[Optional[str], typer.Option("--cursor", "-c", help="Resume cursor")] = None,
    param: Annotated[Optional[list[str]], typer.Option("--param", "-p", help="Parameter as key=value")] = None,
    json_output: JsonFlag = False,
):
    """Run a state-doc flow synchronously.

    \b
    Examples:
        keep flow after-write --target %abc123
        keep flow query-resolve -p query="auth patterns"
        keep flow --file review.yaml --target myproject
        keep flow --cursor <token> --budget 5
    """
    if state is None and file is None and cursor is None:
        typer.echo("Error: provide a state name, --file, or --cursor", err=True)
        raise typer.Exit(1)

    flow_params: dict = {}
    if target:
        flow_params["id"] = target
    for p in (param or []):
        if "=" not in p:
            typer.echo(f"Error: param must be key=value, got: {p!r}", err=True)
            raise typer.Exit(1)
        k, v = p.split("=", 1)
        try:
            flow_params[k] = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            flow_params[k] = v

    state_doc_yaml: Optional[str] = None
    if file is not None:
        if file == "-":
            state_doc_yaml = sys.stdin.read()
        else:
            try:
                state_doc_yaml = Path(file).read_text()
            except FileNotFoundError:
                typer.echo(f"Error: file not found: {file}", err=True)
                raise typer.Exit(1)
        if state is None:
            state = "inline"

    if cursor and state is None:
        state = "__cursor__"

    port = _get_port()
    body: dict = {"state": state, "params": flow_params}
    if budget is not None:
        body["budget"] = budget
    if cursor:
        body["cursor_token"] = cursor
    if state_doc_yaml:
        body["state_doc_yaml"] = state_doc_yaml

    data = _post(port, "/v1/flow", body)
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False))
    else:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("edit")
def edit_cmd(
    id: Annotated[str, typer.Argument(help="ID of note to edit")],
):
    """Edit a note's content in $EDITOR.

    \b
    Opens the current content in your editor. On save, updates the note
    if the content changed.
    Examples:
        keep edit .ignore                    # Edit ignore patterns
        keep edit .prompt/agent/reflect      # Edit a prompt template
        keep edit now                        # Edit current intentions
    """
    import tempfile
    import subprocess as sp

    port = _get_port()
    data = _get(port, f"/v1/notes/{_q(id)}")
    content = data.get("summary", "")

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    suffix = ".md" if not id.endswith((".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml")) else ""
    with tempfile.NamedTemporaryFile(suffix=suffix or Path(id).suffix, mode="w", delete=False, prefix="keep-edit-") as f:
        f.write(content)
        tmp = f.name

    try:
        sp.run([editor, tmp], check=True)
        new_content = Path(tmp).read_text()
    except (sp.CalledProcessError, KeyboardInterrupt):
        typer.echo("Editor exited abnormally, no changes saved", err=True)
        raise typer.Exit(1)
    finally:
        Path(tmp).unlink(missing_ok=True)

    if new_content == content:
        typer.echo("No changes", err=True)
        return

    _post(port, "/v1/notes", {"content": new_content, "id": id})
    typer.echo(f"Updated {id}", err=True)


@app.command()
def analyze(
    id: Annotated[str, typer.Argument(help="ID of note to analyze")],
    tag: Annotated[Optional[list[str]], typer.Option("--tag", "-t", help="Guidance tag keys")] = None,
    foreground: Annotated[bool, typer.Option("--foreground", "--fg", help="Run in foreground")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-analyze even if current")] = False,
    json_output: JsonFlag = False,
):
    """Decompose a note or string into meaningful parts.

    \b
    Uses an LLM to identify sections, each with its own summary, tags,
    and embedding. Runs in background by default; use --fg to wait.
    """
    port = _get_port()
    body: dict = {"id": id, "foreground": foreground, "force": force}
    if tag:
        body["tags"] = tag
    data = _post(port, "/v1/analyze", body)

    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        if data.get("parts") is not None:
            parts = data["parts"]
            if parts:
                typer.echo(f"Analyzed {id} into {len(parts)} parts:")
                for p in parts:
                    summary = str(p.get("summary", ""))[:60].replace("\n", " ")
                    typer.echo(f"  @P{{{p.get('part_num', '?')}}} {summary}")
            else:
                typer.echo(f"Content not decomposable into multiple parts: {id}")
        elif data.get("queued"):
            typer.echo(f"Queued {id} for background analysis.", err=True)
        elif data.get("skipped"):
            typer.echo(f"Already analyzed, skipping {id}.", err=True)


@app.command("help")
def help_cmd(
    topic: Annotated[Optional[str], typer.Argument(help="Documentation topic")] = None,
):
    """Browse keep documentation.

    \b
    Examples:
        keep help              # Documentation index
        keep help quickstart   # CLI Quick Start guide
        keep help keep-put     # keep put reference
    """
    from keep.help import get_help_topic
    typer.echo(get_help_topic(topic or "index", link_style="cli"))


# ---------------------------------------------------------------------------
# Delegate commands — these stay with the full CLI
# ---------------------------------------------------------------------------

@app.command(context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
def pending(ctx: typer.Context):
    """Process pending background tasks."""
    from keep.cli import app as full_app
    full_app(["pending"] + ctx.args, standalone_mode=False)


@app.command()
def config(
    ctx: typer.Context,
    path: Annotated[Optional[str], typer.Argument(
        help="Config path (e.g., 'file', 'tool', 'store', 'providers.embedding')"
    )] = None,
    setup: Annotated[bool, typer.Option("--setup", help="Run setup wizard")] = False,
    reset_system_docs: Annotated[bool, typer.Option("--reset-system-docs", hidden=True)] = False,
    json_output: JsonFlag = False,
):
    """Show configuration. Optionally get a specific value by path.

    \b
    Special paths: file, tool, store, docs, mcpb, providers
    Dotted paths: providers.embedding, tags, etc.
    """
    from keep.cli import app as full_app
    # Check both local flag and parent context flag
    is_json = json_output or (ctx.parent and ctx.parent.params.get("json_output", False))
    args = []
    if is_json:
        args.append("--json")
    args.append("config")
    if path:
        args.append(path)
    if setup:
        args.append("--setup")
    if reset_system_docs:
        args.append("--reset-system-docs")
    try:
        full_app(args, standalone_mode=True)
    except SystemExit as e:
        if e.code:
            raise typer.Exit(e.code)


@app.command(hidden=True)
def doctor(ctx: typer.Context):
    """Diagnostic checks (delegates to full CLI)."""
    from keep.cli import app as full_app
    full_app(["doctor"] + ctx.args, standalone_mode=False)


@app.command(hidden=True, deprecated=True)
def validate(ctx: typer.Context):
    """Validate system documents (delegates to full CLI)."""
    from keep.cli import app as full_app
    full_app(["validate"] + ctx.args, standalone_mode=False)


@app.command()
def mcp(ctx: typer.Context):
    """Start MCP stdio server."""
    from keep.cli import app as full_app
    full_app(["mcp"] + (ctx.args or []), standalone_mode=False)


# ---------------------------------------------------------------------------
# Data management subcommands — delegate to full CLI for file I/O
# ---------------------------------------------------------------------------

data_app = typer.Typer(
    name="data",
    help="Data management — export, import.",
    context_settings={"allow_extra_args": True, "allow_interspersed_args": True},
    rich_markup_mode=None,
)
app.add_typer(data_app)


@data_app.callback(invoke_without_command=True)
def data_callback(ctx: typer.Context):
    """Delegates to full CLI."""
    from keep.cli import app as full_app
    args = ["data"] + (ctx.invoked_subcommand or "").split() + ctx.args
    full_app([a for a in args if a], standalone_mode=False)


def main():
    app()


if __name__ == "__main__":
    main()
