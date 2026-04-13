#!/usr/bin/env python3
"""Explicit host-MCP integration checks for keep.

This script is intentionally separate from the regular test suite.
It validates host-native MCP attachment points and can run opt-in
smoke commands against real installed CLIs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


HOSTS = ("codex", "claude", "kiro", "github_copilot", "openclaw")


@dataclass
class CheckResult:
    """One host integration check result."""

    host: str
    kind: str
    status: str
    detail: str


def resolve_expected_store(store_arg: str | None) -> str:
    """Resolve the expected keep store path for explicit --store launch args."""
    if store_arg:
        return str(Path(store_arg).expanduser().resolve())
    from_env = os.environ.get("KEEP_STORE_PATH")
    if from_env:
        return str(Path(from_env).expanduser().resolve())
    return str((Path.home() / ".keep").resolve())


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a host-side smoke command and capture output."""
    return subprocess.run(args, text=True, capture_output=True, check=False)


def check_codex_config(expected_store: str) -> CheckResult:
    """Validate Codex's native MCP config block when present."""
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return CheckResult("codex", "config", "skip", f"missing {config_path}")
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    keep_cfg = (data.get("mcp_servers") or {}).get("keep")
    if not isinstance(keep_cfg, dict):
        return CheckResult("codex", "config", "fail", "missing [mcp_servers.keep]")
    command = keep_cfg.get("command")
    args = keep_cfg.get("args")
    if command != "keep":
        return CheckResult("codex", "config", "fail", f"expected command=keep, found {command!r}")
    if args != ["--store", expected_store, "mcp"]:
        return CheckResult(
            "codex",
            "config",
            "fail",
            f"expected args=['--store', '{expected_store}', 'mcp'], found {args!r}",
        )
    return CheckResult("codex", "config", "pass", str(config_path))


def check_copilot_config(expected_store: str) -> CheckResult:
    """Validate GitHub Copilot CLI's MCP config file."""
    config_path = Path.home() / ".copilot" / "mcp-config.json"
    if not config_path.exists():
        return CheckResult("github_copilot", "config", "skip", f"missing {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CheckResult("github_copilot", "config", "fail", f"invalid JSON: {exc}")

    keep_cfg = (data.get("mcpServers") or {}).get("keep")
    if not isinstance(keep_cfg, dict):
        return CheckResult("github_copilot", "config", "fail", "missing mcpServers.keep")
    expected = {
        "type": "local",
        "command": "keep",
        "args": ["--store", expected_store, "mcp"],
        "tools": ["*"],
    }
    if keep_cfg != expected:
        return CheckResult("github_copilot", "config", "fail", f"unexpected keep config: {keep_cfg!r}")
    return CheckResult("github_copilot", "config", "pass", str(config_path))


def check_openclaw_bundle() -> CheckResult:
    """Validate that the installed OpenClaw keep plugin has its MCP files."""
    plugin_dir = Path.home() / ".openclaw" / "extensions" / "keep"
    required = [
        plugin_dir / "openclaw.plugin.json",
        plugin_dir / ".mcp.json",
        plugin_dir / "dist" / "index.js",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return CheckResult("openclaw", "config", "fail", f"missing plugin files: {', '.join(missing)}")
    return CheckResult("openclaw", "config", "pass", str(plugin_dir))


def maybe_run(host: str, args: list[str], enabled: bool) -> CheckResult:
    """Run a host-side smoke command when requested."""
    binary = shutil.which(args[0])
    if not binary:
        return CheckResult(host, "smoke", "skip", f"{args[0]} not installed")
    if not enabled:
        return CheckResult(host, "smoke", "skip", "use --run to execute host CLI smoke checks")

    proc = run_command(args)
    if proc.returncode == 0:
        output = proc.stdout.strip() or proc.stderr.strip() or "ok"
        return CheckResult(host, "smoke", "pass", output)
    output = proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}"
    return CheckResult(host, "smoke", "fail", output)


def collect_results(hosts: list[str], expected_store: str, run_smoke: bool) -> list[CheckResult]:
    """Collect config and smoke results for the selected hosts."""
    results: list[CheckResult] = []
    for host in hosts:
        if host == "codex":
            results.append(check_codex_config(expected_store))
            results.append(maybe_run("codex", ["codex", "mcp", "get", "keep"], run_smoke))
            continue
        if host == "claude":
            results.append(
                maybe_run("claude", ["claude", "mcp", "get", "keep"], run_smoke),
            )
            continue
        if host == "kiro":
            results.append(
                maybe_run(
                    "kiro",
                    [
                        "kiro-cli",
                        "chat",
                        "--require-mcp-startup",
                        "--no-interactive",
                        "--trust-all-tools",
                        "Reply with OK.",
                    ],
                    run_smoke,
                ),
            )
            continue
        if host == "github_copilot":
            results.append(check_copilot_config(expected_store))
            continue
        if host == "openclaw":
            results.append(check_openclaw_bundle())
            results.append(maybe_run("openclaw", ["openclaw", "plugins", "inspect", "keep"], run_smoke))
            continue
        raise ValueError(f"unsupported host {host!r}")
    return results


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Explicit keep MCP checks against host-native configs and optional real CLIs.",
    )
    parser.add_argument(
        "--host",
        action="append",
        choices=HOSTS,
        dest="hosts",
        help="Host to check. Repeat to select multiple hosts. Defaults to all.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run host-side smoke commands in addition to config checks.",
    )
    parser.add_argument(
        "--store",
        help="Expected resolved keep store path. Defaults to KEEP_STORE_PATH or ~/.keep.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the selected checks and return a shell exit code."""
    args = parse_args()
    expected_store = resolve_expected_store(args.store)
    selected_hosts = args.hosts or list(HOSTS)
    results = collect_results(selected_hosts, expected_store, args.run)

    if args.json:
        payload = [
            {
                "host": result.host,
                "kind": result.kind,
                "status": result.status,
                "detail": result.detail,
            }
            for result in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(f"Expected keep store: {expected_store}")
        for result in results:
            print(f"[{result.status.upper()}] {result.host}:{result.kind} {result.detail}")

    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
