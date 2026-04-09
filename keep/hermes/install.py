"""Install the Keep memory provider plugin into a Hermes Agent installation.

Usage:
    python -m keep.hermes.install [--hermes-home PATH]

Copies the plugin shim (keep/hermes/plugin/) into Hermes's
plugins/memory/keep/ directory. After install, run:
    hermes config set memory.provider keep
    hermes memory setup
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _find_hermes_plugins_dir(hermes_home: str | None = None) -> Path | None:
    """Locate the Hermes plugins/memory/ directory.

    Search order:
    1. Explicit --hermes-home argument
    2. HERMES_HOME environment variable
    3. hermes-agent in the same venv (importable)
    4. ~/.hermes as fallback
    """
    if hermes_home:
        return Path(hermes_home)

    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)

    # Try to find hermes-agent's repo root via import
    try:
        import plugins.memory as _pm
        return Path(_pm.__file__).parent
    except ImportError:
        pass

    # Fallback: look for common locations
    for candidate in [
        Path.home() / ".hermes",
        Path.home() / "hermes-agent",
    ]:
        plugins_dir = candidate / "plugins" / "memory"
        if plugins_dir.is_dir():
            return plugins_dir

    return None


def install(hermes_home: str | None = None) -> Path:
    """Copy the plugin into Hermes's plugins/memory/keep/ directory.

    Returns the destination path.
    """
    # Source: the plugin/ subdirectory next to this file
    source = Path(__file__).parent / "plugin"
    if not source.is_dir():
        raise FileNotFoundError(f"Plugin source not found: {source}")

    plugins_dir = _find_hermes_plugins_dir(hermes_home)
    if plugins_dir is None:
        raise FileNotFoundError(
            "Cannot find Hermes plugins directory. "
            "Set HERMES_HOME or pass --hermes-home."
        )

    # Handle both "pointing at plugins/memory/" and "pointing at HERMES_HOME"
    if plugins_dir.name == "memory" and plugins_dir.parent.name == "plugins":
        dest = plugins_dir / "keep"
    elif (plugins_dir / "plugins" / "memory").is_dir():
        dest = plugins_dir / "plugins" / "memory" / "keep"
    else:
        raise FileNotFoundError(
            f"No plugins/memory/ directory found under {plugins_dir}. "
            "Is this a Hermes Agent installation?"
        )

    # Copy, overwriting any existing install
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

    return dest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Keep memory provider plugin into Hermes Agent"
    )
    parser.add_argument(
        "--hermes-home",
        help="Path to Hermes home or plugins/memory/ directory",
    )
    args = parser.parse_args()

    try:
        dest = install(args.hermes_home)
        print(f"Keep plugin installed to: {dest}")
        print()
        print("Next steps:")
        print("  hermes config set memory.provider keep")
        print("  hermes memory setup")
    except (FileNotFoundError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
