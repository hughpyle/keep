from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from keep.config import StoreConfig, create_default_config, save_config
from keep.integrations import (
    PROTOCOL_BLOCK_MARKER,
    check_and_install,
    install_codex,
    install_github_copilot,
)


def test_install_codex_writes_global_codex_agents_file(tmp_path):
    codex_dir = tmp_path / ".codex"

    actions = install_codex(codex_dir)

    assert actions == ["protocol block"]
    agents_md = codex_dir / "AGENTS.md"
    assert agents_md.exists()
    assert PROTOCOL_BLOCK_MARKER in agents_md.read_text(encoding="utf-8")


def test_check_and_install_does_not_modify_cwd_agents_md(tmp_path, monkeypatch):
    cwd_agents = tmp_path / "AGENTS.md"
    cwd_agents.write_text("# Repo instructions\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("keep.integrations.detect_new_tools", lambda already_known: {})

    config = StoreConfig(path=Path(tmp_path), integrations={})
    check_and_install(config)

    assert cwd_agents.read_text(encoding="utf-8") == "# Repo instructions\n"
    assert PROTOCOL_BLOCK_MARKER not in cwd_agents.read_text(encoding="utf-8")


def test_get_keeper_does_not_auto_install_integrations(mock_providers, tmp_path, monkeypatch):
    config = create_default_config(tmp_path)
    save_config(config)
    monkeypatch.setenv("KEEP_CONFIG", str(tmp_path))

    with patch("keep.integrations.check_and_install", side_effect=AssertionError("should not be called")):
        from keep.console_support import _get_keeper

        kp = _get_keeper(tmp_path)

    try:
        assert kp is not None
    finally:
        kp.close()


def test_install_github_copilot_bakes_in_resolved_store_path(tmp_path, monkeypatch):
    copilot_dir = tmp_path / ".copilot"
    expected_store = str(Path("/tmp/hermes-store").resolve())
    monkeypatch.setenv("KEEP_STORE_PATH", "/tmp/hermes-store")

    actions = install_github_copilot(copilot_dir)

    assert actions == ["MCP server"]
    mcp_json = copilot_dir / "mcp-config.json"
    assert mcp_json.exists()
    content = mcp_json.read_text(encoding="utf-8")
    assert '"command": "keep"' in content
    assert '"args": [' in content
    assert '"--store"' in content
    assert f'"{expected_store}"' in content
    assert '"mcp"' in content


def test_install_github_copilot_is_idempotent_when_entry_is_current(tmp_path, monkeypatch):
    copilot_dir = tmp_path / ".copilot"
    monkeypatch.setenv("KEEP_STORE_PATH", "/tmp/hermes-store")

    assert install_github_copilot(copilot_dir) == ["MCP server"]
    assert install_github_copilot(copilot_dir) == []
