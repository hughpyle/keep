"""Hatch build hook: verify the OpenClaw plugin bundle is present.

`scripts/bump_version.py` rebuilds `keep/data/openclaw-plugin/dist/index.js`
before `uv build` runs, and `pyproject.toml` force-includes that file in
the wheel. This hook is a safety check: if the bundled artifact is missing,
fail the build loudly rather than shipping a broken plugin.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class OpenClawPluginBuildHook(BuildHookInterface):
    """Verify the pre-built OpenClaw plugin entry point is present."""

    PLUGIN_NAME = "openclaw-plugin-build"

    def initialize(self, version, build_data):
        plugin_dir = Path(self.root) / "keep" / "data" / "openclaw-plugin"
        dist_file = plugin_dir / "dist" / "index.js"

        if not dist_file.exists():
            raise RuntimeError(
                f"OpenClaw plugin not built: {dist_file} is missing. "
                f"Run `node build.mjs` in {plugin_dir}, or release via "
                "`scripts/release.sh` which rebuilds it automatically."
            )

        self.app.display_info(f"OpenClaw plugin present: {dist_file}")
