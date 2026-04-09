"""CLI commands for Keep integration management.

Minimal surface:
  - ``hermes keep status``: show profile, store path, config state, daemon state
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path

from hermes_constants import get_hermes_home

_KEEP_CONFIG_FILENAME = "keep.toml"
_DAEMON_PORT_FILE = ".daemon.port"
_DAEMON_TOKEN_FILE = ".daemon.token"


def _display_path(path: Path) -> str:
    """Format a path with ``~`` shorthand when possible."""
    try:
        return str(Path("~") / path.resolve().relative_to(Path.home()))
    except Exception:
        return str(path)


def _store_path() -> Path:
    """Resolve the Keep store path using Hermes conventions."""
    return Path(os.environ.get("KEEP_STORE_PATH") or (get_hermes_home() / "keep")).resolve()


def _config_state(store_path: Path) -> str:
    """Return a compact config state for the resolved store."""
    config_path = store_path / _KEEP_CONFIG_FILENAME
    if not config_path.exists():
        return "missing"

    try:
        import tomllib

        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return "invalid"

    embedding_name = str(data.get("embedding", {}).get("name") or "").strip()
    return "configured" if embedding_name else "setup required"


def _daemon_state(store_path: Path) -> str:
    """Return the current daemon state without auto-starting it."""
    port_path = store_path / _DAEMON_PORT_FILE
    token_path = store_path / _DAEMON_TOKEN_FILE
    if not port_path.exists():
        return "not running"

    try:
        port = int(port_path.read_text().strip())
    except (OSError, ValueError):
        return "not reachable"

    headers: dict[str, str] = {}
    if token_path.exists():
        try:
            token = token_path.read_text().strip()
        except OSError:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/v1/ready", headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
    except Exception:
        return "not reachable"

    if resp.status != 200:
        return "not reachable"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "not reachable"

    if payload.get("status") == "ok":
        return "running"
    return "not reachable"


def cmd_status(args) -> None:
    """Show current Keep status for the active Hermes profile."""
    from hermes_cli.profiles import get_active_profile_name

    profile_name = get_active_profile_name()
    store_path = _store_path()

    print("\nKeep status")
    print("-" * 40)
    print(f"  Profile:      {profile_name}")
    print(f"  Store path:   {_display_path(store_path)}")
    print(f"  Config state: {_config_state(store_path)}")
    print(f"  Daemon state: {_daemon_state(store_path)}")
    print()


def keep_command(args) -> None:
    """Route Keep subcommands."""
    cmd_status(args)


def register_cli(subparser) -> None:
    """Build the ``hermes keep`` argparse subcommand tree."""
    subs = subparser.add_subparsers(dest="keep_cli_command")
    subs.add_parser("status", help="Show Keep profile, store, config, and daemon state")
    subparser.set_defaults(func=keep_command)
