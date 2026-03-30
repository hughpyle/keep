"""Tests for daemon startup sequencing and deferred maintenance."""

import sys
from runpy import run_path
from unittest.mock import patch

from keep.api import Keeper


def test_keeper_deferred_startup_skips_scans_until_started(mock_providers, tmp_path):
    calls: list[str] = []

    def fake_marker(self, chroma_coll, doc_coll, *, _doc_store=None):
        calls.append("marker")

    def fake_check(self, *, _doc_store=None):
        calls.append("reconcile-check")
        return False

    with (
        patch.object(Keeper, "_run_tag_marker_startup_check", fake_marker),
        patch.object(Keeper, "_check_store_consistency", fake_check),
    ):
        kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
        try:
            assert calls == []

            kp._run_deferred_startup_maintenance()

            assert calls == ["marker", "reconcile-check"]
        finally:
            kp.close()


def test_start_deferred_startup_maintenance_starts_once(mock_providers, tmp_path):
    with patch.object(Keeper, "_run_deferred_startup_maintenance", return_value=None) as runner:
        kp = Keeper(store_path=tmp_path, defer_startup_maintenance=True)
        try:
            assert kp.start_deferred_startup_maintenance() is True
            assert kp.start_deferred_startup_maintenance() is False
            assert kp._startup_maintenance_thread is not None
            kp._startup_maintenance_thread.join(timeout=2)
            runner.assert_called_once()
        finally:
            kp.close()


def test_daemon_entrypoint_uses_deferred_startup_maintenance(tmp_path):
    captured: dict[str, object] = {}

    class DummyKeeper:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    with (
        patch("keep.api.Keeper", DummyKeeper),
        patch("keep.cli.run_pending_daemon"),
        patch.object(sys, "argv", ["python", "--store", str(tmp_path)]),
    ):
        from keep import daemon

        daemon.main()

    kwargs = captured["kwargs"]
    assert kwargs["store_path"] == str(tmp_path)
    assert kwargs["defer_startup_maintenance"] is True


def test_daemon_script_entrypoint_supports_direct_python_execution(tmp_path):
    captured: dict[str, object] = {}

    class DummyKeeper:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    with (
        patch("keep.api.Keeper", DummyKeeper),
        patch("keep.cli.run_pending_daemon"),
        patch.object(sys, "argv", ["python", "--store", str(tmp_path)]),
    ):
        run_path("keep/daemon.py", run_name="__main__")

    kwargs = captured["kwargs"]
    assert kwargs["store_path"] == str(tmp_path)
    assert kwargs["defer_startup_maintenance"] is True
