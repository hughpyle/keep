"""Daemon entry point: python -m keep.daemon --store PATH.

Minimal argument parsing — no typer, no CLI framework.
Starts a Keeper and runs the daemon loop directly.
"""

import argparse
import sys
from pathlib import Path


def _load_daemon_runtime():
    """Load Keeper and run_pending_daemon for module or script execution."""
    try:
        from .api import Keeper
        from .console_support import run_pending_daemon
        return Keeper, run_pending_daemon
    except ImportError:
        if __package__ not in (None, ""):
            raise
        repo_root = Path(__file__).resolve().parent.parent
        repo_root_str = str(repo_root)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)
        from keep.api import Keeper
        from keep.console_support import run_pending_daemon
        return Keeper, run_pending_daemon


def main():
    parser = argparse.ArgumentParser(description="keep daemon")
    parser.add_argument("--store", required=True, help="Store path")
    args = parser.parse_args()

    Keeper, run_pending_daemon = _load_daemon_runtime()
    kp = Keeper(store_path=args.store, defer_startup_maintenance=True)
    run_pending_daemon(kp)


if __name__ == "__main__":
    main()
