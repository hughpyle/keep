"""Daemon entry point for ``keepd`` or ``python -m keep.daemon --store PATH``.

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
        from .model_lock import ModelLock
        return Keeper, run_pending_daemon, ModelLock
    except ImportError:
        if __package__ not in (None, ""):
            raise
        repo_root = Path(__file__).resolve().parent.parent
        repo_root_str = str(repo_root)
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)
        from keep.api import Keeper
        from keep.console_support import run_pending_daemon
        from keep.model_lock import ModelLock
        return Keeper, run_pending_daemon, ModelLock


def main():
    parser = argparse.ArgumentParser(description="keep daemon")
    parser.add_argument("--store", required=True, help="Store path")
    parser.add_argument("--bind", default=None, help="Bind host for the daemon HTTP server")
    parser.add_argument(
        "--advertised-url",
        default=None,
        help="Advertised base URL used for remote-mode host allowlisting",
    )
    parser.add_argument(
        "--trusted-proxy",
        action="store_true",
        help="Acknowledge that TLS is terminated by a trusted reverse proxy",
    )
    args = parser.parse_args()

    Keeper, run_pending_daemon, ModelLock = _load_daemon_runtime()
    store_path = Path(args.store).expanduser().resolve()
    processor_lock = ModelLock(store_path / ".processor.lock")
    if not processor_lock.acquire(blocking=False):
        return

    try:
        kp = Keeper(store_path=args.store, defer_startup_maintenance=True)
        run_pending_daemon(
            kp,
            processor_lock=processor_lock,
            bind_host=args.bind,
            advertised_url=args.advertised_url,
            trusted_proxy=args.trusted_proxy,
        )
    finally:
        processor_lock.release()


if __name__ == "__main__":
    main()
