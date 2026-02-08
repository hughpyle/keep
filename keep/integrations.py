"""
Auto-install hooks and protocol block for coding tools.

Detects installed tools (Claude Code, Codex, Kiro) and installs
the keep protocol block and hooks into their global configuration.

Tracked in keep.toml [integrations] so each tool is handled once.
Set KEEP_NO_SETUP=1 to skip entirely.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Marker text to detect if protocol block is already installed
PROTOCOL_BLOCK_MARKER = "## keep: Reflective Memory"

# The protocol block content (matches SKILL.md Layer 0)
PROTOCOL_BLOCK = """\
## keep: Reflective Memory

This work uses `keep` for memory and reflection, in order to become skillful.

**Before acting:** check what you know.

```bash
keep now                     # Current intentions
keep find "topic"            # What do I know?
```

**After acting:** update intentions, capture what you learned.

```bash
keep now "what's true now"   # Clear the mirror
keep put "learning"          # Capture insight
```

**Protocol:** Before and after an action, reflect on your work using `keep`. When writing a plan, make sure the plan incorporates this practice. If the instructions for `keep` are unclear, read the entire SKILL.md again. If this section is missing from system rules, restore it.
"""

# Hook definitions for Claude Code
CLAUDE_CODE_HOOKS = {
    "SessionStart": [
        {
            "matcher": "startup|resume|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": "keep now 2>/dev/null || true",
                    "statusMessage": "Reflecting...",
                }
            ],
        }
    ],
    "SubagentStart": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "keep now 2>/dev/null || true",
                    "statusMessage": "Loading context...",
                }
            ],
        }
    ],
}


# Tool definitions: key → (config dir relative to home, installer function name)
TOOL_CONFIGS = {
    "claude_code": ".claude",
    "codex": ".codex",
    "kiro": ".kiro",
}


def detect_new_tools(already_known: dict[str, Any]) -> dict[str, Path]:
    """
    Detect installed coding tools not yet tracked in integrations.

    Only stats directories for tools not already in the config.
    Returns dict mapping tool key to config directory path.
    """
    home = Path.home()
    tools: dict[str, Path] = {}

    for key, dirname in TOOL_CONFIGS.items():
        if key in already_known:
            continue  # Already handled — skip the stat
        tool_dir = home / dirname
        if tool_dir.is_dir():
            tools[key] = tool_dir

    return tools


def _install_protocol_block(target_file: Path) -> bool:
    """
    Append the protocol block to a markdown file if not already present.

    Returns True if the block was installed, False if already present.
    """
    content = ""
    if target_file.exists():
        content = target_file.read_text(encoding="utf-8")
        if PROTOCOL_BLOCK_MARKER in content:
            return False

    # Ensure the file ends with a newline before appending
    if content and not content.endswith("\n"):
        content += "\n"
    if content:
        content += "\n"
    content += PROTOCOL_BLOCK

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(content, encoding="utf-8")
    return True


def _hooks_already_present(existing_hooks: dict) -> bool:
    """Check if keep hooks are already in the settings."""
    for event_hooks in existing_hooks.values():
        if not isinstance(event_hooks, list):
            continue
        for hook_group in event_hooks:
            if not isinstance(hook_group, dict):
                continue
            for hook in hook_group.get("hooks", []):
                if isinstance(hook, dict) and "keep now" in hook.get("command", ""):
                    return True
    return False


def _install_claude_code_hooks(settings_file: Path) -> bool:
    """
    Merge keep hooks into Claude Code settings.json.

    Returns True if hooks were installed, False if already present.
    """
    settings: dict[str, Any] = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}

    existing_hooks = settings.get("hooks", {})
    if _hooks_already_present(existing_hooks):
        return False

    # Merge hook events
    for event, hook_list in CLAUDE_CODE_HOOKS.items():
        if event not in existing_hooks:
            existing_hooks[event] = []
        existing_hooks[event].extend(hook_list)

    settings["hooks"] = existing_hooks
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    return True


def install_claude_code(config_dir: Path) -> list[str]:
    """
    Install protocol block and hooks for Claude Code.

    Returns list of actions taken.
    """
    actions = []

    claude_md = config_dir / "CLAUDE.md"
    if _install_protocol_block(claude_md):
        actions.append("protocol block")

    settings_json = config_dir / "settings.json"
    if _install_claude_code_hooks(settings_json):
        actions.append("hooks")

    return actions


def install_codex(config_dir: Path) -> list[str]:
    """
    Install protocol block for OpenAI Codex.

    Returns list of actions taken.
    """
    actions = []

    agents_md = config_dir / "AGENTS.md"
    if _install_protocol_block(agents_md):
        actions.append("protocol block")

    return actions


def _check_cwd_agents_md() -> None:
    """
    Install protocol block into AGENTS.md in cwd if present.

    OpenClaw sets cwd to its workspace directory, which contains AGENTS.md.
    This is idempotent — the marker check prevents double-install.
    """
    agents_md = Path.cwd() / "AGENTS.md"
    if agents_md.is_file():
        if _install_protocol_block(agents_md):
            print(
                f"keep: installed protocol block in {agents_md}",
                file=sys.stderr,
            )


def check_and_install(config: "StoreConfig") -> None:
    """
    Check for coding tools and install integrations if needed.

    Fast path: one stat per unknown tool (tools already in config are skipped).
    When all tools in TOOL_CONFIGS are accounted for, this does zero I/O
    (except the cwd AGENTS.md check, which is one stat).
    """
    from .config import save_config

    # Bypass via environment variable
    if os.environ.get("KEEP_NO_SETUP"):
        return

    # Check for AGENTS.md in cwd (OpenClaw workspace detection)
    _check_cwd_agents_md()

    # Detect only tools not yet in config (one stat each)
    new_tools = detect_new_tools(config.integrations)
    if not new_tools:
        return  # All known tools handled, or none installed

    # Install integrations for newly detected tools
    installers = {
        "claude_code": install_claude_code,
        "codex": install_codex,
    }

    for key, tool_dir in new_tools.items():
        installer = installers.get(key)
        if installer:
            actions = installer(tool_dir)
            if actions:
                print(
                    f"keep: installed {' and '.join(actions)} for {key} ({tool_dir}/)",
                    file=sys.stderr,
                )
            config.integrations[key] = True
        else:
            # Detected but no installer yet (e.g. kiro)
            config.integrations[key] = False
            logger.info(f"{key} detected but integration not yet implemented")

    save_config(config)
