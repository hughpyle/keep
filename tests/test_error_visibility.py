"""Focused coverage for diagnostic logging on best-effort fallbacks."""

import logging
from types import SimpleNamespace
from unittest.mock import patch

from keep.api import Keeper
from keep.console_support import _render_context_from_flow_bindings
from keep.daemon_client import check_health


def test_embed_task_reindex_save_config_failure_is_logged(caplog):
    host = SimpleNamespace(
        _config=SimpleNamespace(embed_task_reindex_done=False),
        _config_uses_embed_task=lambda: False,
    )

    caplog.set_level(logging.WARNING, logger="keep.api")
    with patch("keep.api.save_config", side_effect=OSError("disk full")):
        Keeper._enqueue_embed_task_reindex(host)

    assert host._config.embed_task_reindex_done is True
    assert "Failed to persist embed_task_reindex_done" in caplog.text
    assert "disk full" in caplog.text


def test_context_binding_fallback_logs_context_failure(caplog):
    class BrokenContextHost:
        def get_context(self, item_id):
            raise RuntimeError(f"context failed for {item_id}")

    bindings = {"item": {"id": "note-1", "summary": "fallback summary", "tags": {}}}
    caplog.set_level(logging.INFO, logger="keep.console_support")

    output = _render_context_from_flow_bindings(bindings, BrokenContextHost())

    assert "fallback summary" in output
    assert "Failed to render full context for note-1" in caplog.text
    assert "context failed for note-1" in caplog.text


def test_daemon_health_check_failure_is_debug_logged(caplog):
    caplog.set_level(logging.DEBUG, logger="keep.daemon_client")
    with patch("keep.daemon_client.http.client.HTTPConnection", side_effect=OSError("no daemon")):
        assert check_health(43210) is False

    assert "Daemon health check failed for port 43210" in caplog.text
    assert "no daemon" in caplog.text
