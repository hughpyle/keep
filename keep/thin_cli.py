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
    return (config_dir / "store").resolve()


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
    """Collapse newlines and truncate."""
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def _date(tags: dict) -> str:
    """Extract display date from tags."""
    for key in ("_updated", "_created"):
        val = tags.get(key, "")
        if val and len(val) >= 10:
            return val[:10]
    return ""


def _display_tags(tags: dict) -> dict:
    """Filter to user-visible tags.

    NOTE: this skip set must stay in sync with keep/types.py:INTERNAL_TAGS
    and the system tag conventions in keep/api.py.
    """
    skip = {"_created", "_updated", "_accessed", "_updated_date", "_accessed_date",
            "_content_type", "_source", "_session", "_content_length", "_content_hash",
            "_summarized_hash", "_base_id", "_part_num", "_version",
            "_focus_part", "_focus_version", "_focus_summary", "_focus_start_line",
            "_focus_end_line", "_lane", "_anchor_id", "_anchor_type",
            "_analyzed_version", "_file_mtime_ns", "_file_size", "_entity",
            "_supernode_reviewed", "_version_edges"}
    return {k: v for k, v in tags.items() if k not in skip and not k.startswith("_tk::") and not k.startswith("_tv::")}


def _render_tags_block(tags: dict) -> str:
    """Render tags as YAML-style indented block."""
    display = _display_tags(tags)
    if not display:
        return ""
    lines = []
    for k, v in sorted(display.items()):
        if isinstance(v, list):
            lines.append(f"  {k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _render_item_line(item: dict, w: int) -> str:
    """Format a single item as a summary line."""
    id = item.get("id", "")
    score = item.get("score")
    tags = item.get("tags", {})
    date = _date(tags)
    summary = _truncate(item.get("summary", ""), w - len(id) - 20)

    parts = [f"  {id}"]
    if score is not None:
        parts.append(f"({score:.2f})")
    if date:
        parts.append(date)
    parts.append(summary)
    return " ".join(parts)


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
        lines.append("similar:")
        for s in similar:
            sid = s.get("id", "")
            score = s.get("score")
            date = s.get("date", "")
            summary = _truncate(s.get("summary", ""), w - len(sid) - 25)
            score_str = f" ({score:.2f})" if score is not None else ""
            date_str = f" {date}" if date else ""
            lines.append(f"  - {sid}{score_str}{date_str} {summary}")

    # Meta sections
    meta = data.get("meta", {})
    for section, items in sorted(meta.items()):
        if items:
            lines.append(f"meta/{section}:")
            for m in items:
                mid = m.get("id", "")
                msummary = _truncate(m.get("summary", ""), w - len(mid) - 10)
                lines.append(f"  - {mid} {msummary}")

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

    # Parts
    parts = data.get("parts", [])
    if parts:
        lines.append("parts:")
        for p in parts:
            pnum = p.get("part_num", 0)
            psummary = _truncate(p.get("summary", ""), w - 15)
            lines.append(f"  - @P{{{pnum}}} {psummary}")

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

    lines = []
    for item in notes:
        lines.append(_render_item_line(item, w))
        # Deep group items
        item_id = item.get("id", "")
        base_id = item_id.split("@")[0] if "@" in item_id else item_id
        group = deep_groups.get(base_id, deep_groups.get(item_id, []))
        for deep in group:
            deep_line = _render_item_line(deep, w - 4)
            lines.append(f"    {deep_line.strip()}")

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
    id: Annotated[str, typer.Argument(help="Item ID")] = "now",
    version: Annotated[Optional[int], typer.Option("-V", "--version", help="Version offset")] = None,
    limit: LimitOption = 3,
    json_output: JsonFlag = False,
):
    """Show an item with context."""
    port = _get_port()
    params = f"?similar_limit={limit}&meta_limit={limit}&edges_limit={limit}"
    if version is not None:
        params += f"&version={version}"
    data = _get(port, f"/v1/notes/{_q(id)}/context{params}")
    if json_output:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_render_context(data))


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
    """Search memory."""
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
    content: Annotated[str, typer.Argument(help="Content or file URI")],
    id: Annotated[Optional[str], typer.Option("--id", "-i", help="Item ID")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Tags (key=value)")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", help="Explicit summary")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force update")] = False,
    json_output: JsonFlag = False,
):
    """Store content in memory."""
    port = _get_port()
    parsed_tags = {}
    for t in (tags or []):
        if "=" in t:
            k, v = t.split("=", 1)
            parsed_tags[k] = v

    # Detect file/URL
    uri = None
    if content.startswith("file://") or content.startswith("http://") or content.startswith("https://"):
        uri = content
        content = None
    elif Path(content).exists() and not content.startswith("%"):
        uri = f"file://{Path(content).resolve()}"
        content = None

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


@app.command()
def tag(
    id: Annotated[str, typer.Argument(help="Item ID")],
    tags: Annotated[list[str], typer.Argument(help="Tags (key=value or key= to remove)")],
    json_output: JsonFlag = False,
):
    """Set or remove tags."""
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


@app.command("del")
def delete_cmd(
    id: Annotated[str, typer.Argument(help="Item ID")],
):
    """Delete an item."""
    port = _get_port()
    data = _delete(port, f"/v1/notes/{_q(id)}")
    if data.get("deleted"):
        typer.echo(f"{id} deleted.")
    else:
        typer.echo(f"{id} not found.", err=True)


@app.command()
def now(
    content: Annotated[Optional[str], typer.Argument(help="New content")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Tags")] = None,
    json_output: JsonFlag = False,
):
    """Show or update current working context."""
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
    query: Annotated[Optional[str], typer.Argument(help="Search query")] = None,
    tags: Annotated[Optional[list[str]], typer.Option("-t", "--tag", help="Filter by tag")] = None,
    limit: LimitOption = 20,
    since: Annotated[Optional[str], typer.Option("--since")] = None,
    until: Annotated[Optional[str], typer.Option("--until")] = None,
    json_output: JsonFlag = False,
):
    """List items."""
    port = _get_port()
    tag_filter = {}
    for t in (tags or []):
        if "=" in t:
            k, v = t.split("=", 1)
            tag_filter[k] = v
    if not query:
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
            "query": query, "tags": tag_filter or None, "limit": limit,
            "since": since, "until": until,
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
    """Move item to a named collection."""
    port = _get_port()
    data = _post(port, "/v1/flow", {
        "state": "move", "params": {"name": name, "source": source},
    })
    typer.echo(f"Moved to {name}.")


@app.command()
def prompt(ctx: typer.Context):
    """Run an agent prompt (delegates to full CLI)."""
    # TODO: add "prompt" flow state doc, then use POST /v1/flow
    from keep.cli import app as full_app
    full_app(["prompt"] + ctx.args, standalone_mode=False)


@app.command()
def reflect(ctx: typer.Context):
    """Reflect on a topic (delegates to full CLI)."""
    # TODO: add "prompt" flow state doc, then use POST /v1/flow
    from keep.cli import app as full_app
    full_app(["reflect"] + ctx.args, standalone_mode=False)


@app.command(context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
def pending(ctx: typer.Context):
    """Manage background processing (delegates to full CLI)."""
    from keep.cli import app as full_app
    full_app(["pending"] + ctx.args, standalone_mode=False)


@app.command()
def config(
    setup: Annotated[bool, typer.Option("--setup", help="Run setup wizard")] = False,
    json_output: JsonFlag = False,
):
    """Show configuration."""
    if setup:
        from keep.cli import app as full_app
        full_app(["config", "--setup"], standalone_mode=False)
        return

    store_path = _resolve_store_path()
    port_file = store_path / ".daemon.port"
    if port_file.exists():
        try:
            port = int(port_file.read_text().strip())
            health = _get_health(port)
        except Exception:
            health = None
    else:
        health = None

    if json_output and health:
        typer.echo(json.dumps(health, indent=2))
        return

    typer.echo(f"store: {store_path}")
    if health:
        typer.echo(f"daemon: 127.0.0.1:{health.get('pid', '?')} (v{health.get('version', '?')})")
        typer.echo(f"embedding: {health.get('embedding') or 'none'}")
        typer.echo(f"summarization: {health.get('summarization') or 'none'}")
        typer.echo(f"items: {health.get('item_count', 0)}")
        for w in health.get("warnings", []):
            typer.echo(f"warning: {w}", err=True)
    else:
        typer.echo("daemon: not running")


def main():
    app()


if __name__ == "__main__":
    main()
