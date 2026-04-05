from __future__ import annotations

from pathlib import Path

from keep.config import StoreConfig
from keep.integrations import PROTOCOL_BLOCK_MARKER, check_and_install, install_codex


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
