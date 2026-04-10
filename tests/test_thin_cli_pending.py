"""Tests for command-app pending command lifecycle behavior."""

import re
from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from keep import cli_app
from keep.const import DAEMON_PORT_FILE, DAEMON_TOKEN_FILE
from keep.markdown_mirrors import MarkdownMirrorEntry


def test_pending_stop_cleans_stale_discovery_files_without_pid(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / DAEMON_PORT_FILE).write_text("5337")
    (store / DAEMON_TOKEN_FILE).write_text("token")

    with patch("keep.daemon_client.resolve_store_path", return_value=store):
        cli_app.pending(stop=True)

    captured = capsys.readouterr()
    assert "No daemon running." in captured.out
    assert not (store / DAEMON_PORT_FILE).exists()
    assert not (store / DAEMON_TOKEN_FILE).exists()


def test_pending_mentions_active_markdown_mirrors(capsys):
    kp = MagicMock()
    kp.pending_count.return_value = 0
    kp.pending_work_count.return_value = 0
    kp._pending_queue.stats.return_value = {
        "failed": 0, "processing": 0, "pending": 0, "delegated": 0,
    }
    kp._is_processor_running.return_value = True
    kp._store_path = MagicMock()

    with patch("keep.markdown_mirrors.list_markdown_mirrors", return_value=[
        MarkdownMirrorEntry(root="/tmp/vault", enabled=True),
    ]), \
         patch("keep.watches.has_active_watches", return_value=False), \
         patch("keep.console_support._tail_ops_log"), \
         patch("keep.console_support.typer.echo") as echo:
        from keep.console_support import print_pending_interactive

        print_pending_interactive(kp)

    messages = [call.args[0] for call in echo.call_args_list]
    assert "Markdown mirrors active: 1" in messages


def test_root_help_shows_daemon_and_hides_pending_alias():
    runner = CliRunner()

    result = runner.invoke(cli_app.app, ["--help"])

    assert result.exit_code == 0
    assert re.search(r"^\s+daemon\s", result.stdout, re.MULTILINE)
    assert not re.search(r"^\s+pending\s", result.stdout, re.MULTILINE)


def test_daemon_command_runs_foreground_daemon_with_transport_options(tmp_path):
    runner = CliRunner()
    daemon_run: dict[str, object] = {}

    class DummyKeeper:
        def __init__(self, *args, **kwargs):
            pass

    with (
        patch("keep.api.Keeper", DummyKeeper),
        patch(
            "keep.console_support.run_pending_daemon",
            side_effect=lambda *args, **kwargs: daemon_run.update(kwargs),
        ),
    ):
        result = runner.invoke(
            cli_app.app,
            [
                "--store", str(tmp_path),
                "daemon",
                "--bind", "0.0.0.0",
                "--advertised-url", "https://keep.example.test",
                "--trusted-proxy",
            ],
        )

    assert result.exit_code == 0, result.stdout
    assert daemon_run["bind_host"] == "0.0.0.0"
    assert daemon_run["advertised_url"] == "https://keep.example.test"
    assert daemon_run["trusted_proxy"] is True


def test_hidden_pending_alias_still_runs_interactive_mode(tmp_path):
    runner = CliRunner()
    kp = MagicMock()

    with (
        patch("keep.daemon_client.resolve_store_path", return_value=tmp_path),
        patch("keep.api.Keeper", return_value=kp),
        patch("keep.console_support.print_pending_interactive") as interactive,
    ):
        result = runner.invoke(cli_app.app, ["pending"])

    assert result.exit_code == 0, result.stdout
    interactive.assert_called_once_with(kp)
    kp.close.assert_called_once()
