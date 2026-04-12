"""Tests for daemon client discovery and auto-start logic."""

import sys
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keep.const import DAEMON_PORT_FILE, DAEMON_TOKEN_FILE


class TestGetPortNoFileStranding:
    """get_port must not delete discovery files of a still-running daemon."""

    def test_unhealthy_daemon_files_not_deleted(self, tmp_path):
        """When health check fails, discovery files are preserved for recovery.

        Previously, get_port() deleted .daemon.port and .daemon.token
        immediately on health-check failure, then spawned a replacement.
        If the original daemon was alive but briefly unhealthy and the
        replacement exited on .processor.lock, both files were gone and
        no daemon was reachable.
        """
        store = tmp_path / "store"
        store.mkdir()
        port_file = store / DAEMON_PORT_FILE
        token_file = store / DAEMON_TOKEN_FILE
        port_file.write_text("9999")
        token_file.write_text("tok-abc")

        health_calls = []

        def mock_health(port):
            health_calls.append(port)
            # First call: unhealthy.  Second call: recovered.
            return len(health_calls) > 1

        with (
            patch("keep.daemon_client.resolve_store_path", return_value=store),
            patch("keep.daemon_client.check_health", side_effect=mock_health),
            patch("keep.daemon_client.start_daemon"),
            patch("keep.daemon_client._load_token"),
        ):
            from keep.daemon_client import get_port
            port = get_port(str(store))

        assert port == 9999
        # Files must still exist — not deleted
        assert port_file.exists()
        assert token_file.exists()

    def test_dead_daemon_replacement_writes_new_files(self, tmp_path):
        """When old daemon is truly dead, replacement writes new discovery files."""
        store = tmp_path / "store"
        store.mkdir()
        port_file = store / DAEMON_PORT_FILE
        token_file = store / DAEMON_TOKEN_FILE
        # Stale files from dead daemon
        port_file.write_text("8888")
        token_file.write_text("old-token")

        health_calls = []

        def mock_health(port):
            health_calls.append(port)
            # Old port 8888 always unhealthy; new port 7777 healthy
            return port == 7777

        def mock_start(store_path):
            # Simulate replacement daemon writing new files
            port_file.write_text("7777")
            token_file.write_text("new-token")

        with (
            patch("keep.daemon_client.resolve_store_path", return_value=store),
            patch("keep.daemon_client.check_health", side_effect=mock_health),
            patch("keep.daemon_client.start_daemon", side_effect=mock_start),
            patch("keep.daemon_client._load_token"),
        ):
            from keep.daemon_client import get_port
            port = get_port(str(store))

        assert port == 7777

    def test_no_existing_daemon_starts_fresh(self, tmp_path):
        """With no discovery files, get_port spawns a daemon and polls."""
        store = tmp_path / "store"
        store.mkdir()
        port_file = store / DAEMON_PORT_FILE

        def mock_start(store_path):
            port_file.write_text("5555")

        def mock_health(port):
            return port == 5555

        with (
            patch("keep.daemon_client.resolve_store_path", return_value=store),
            patch("keep.daemon_client.check_health", side_effect=mock_health),
            patch("keep.daemon_client.start_daemon", side_effect=mock_start),
            patch("keep.daemon_client._load_token"),
        ):
            from keep.daemon_client import get_port
            port = get_port(str(store))

        assert port == 5555

    def test_new_discovery_files_allow_port_return_before_ready_probe(self, tmp_path):
        """Fresh daemon discovery files should unblock the first real request.

        The daemon may publish a new port/token before /v1/ready answers
        successfully. In that case get_port() should return the fresh port and
        let the caller's actual request perform the next retry.
        """
        store = tmp_path / "store"
        store.mkdir()
        port_file = store / DAEMON_PORT_FILE
        token_file = store / DAEMON_TOKEN_FILE

        def mock_start(store_path):
            port_file.write_text("5555")
            token_file.write_text("new-token")

        with (
            patch("keep.daemon_client.resolve_store_path", return_value=store),
            patch("keep.daemon_client.check_health", return_value=False),
            patch("keep.daemon_client.start_daemon", side_effect=mock_start),
            patch("keep.daemon_client._load_token"),
        ):
            from keep.daemon_client import get_port
            port = get_port(str(store))

        assert port == 5555


class TestLoadTokenCacheScoping:
    """Token cache must be scoped to the resolved store path."""

    def test_load_token_switches_stores_without_force(self, tmp_path):
        store_a = tmp_path / "store-a"
        store_b = tmp_path / "store-b"
        store_a.mkdir()
        store_b.mkdir()
        (store_a / DAEMON_TOKEN_FILE).write_text("token-a")
        (store_b / DAEMON_TOKEN_FILE).write_text("token-b")

        from keep import daemon_client as client

        client._auth_token = ""
        client._auth_token_store = ""
        try:
            token_a = client._load_token(str(store_a))
            token_b = client._load_token(str(store_b))

            assert token_a == "token-a"
            assert token_b == "token-b"
            assert client._auth_token_store == str(store_b.resolve())
        finally:
            client._auth_token = ""
            client._auth_token_store = ""


class TestCheckHealth:
    """Readiness checks must send auth headers correctly."""

    def test_check_health_sends_authorization_header(self):
        from keep import daemon_client as client

        class FakeResponse:
            status = 200

            def read(self):
                return b'{"needs_setup": false, "warnings": []}'

        class FakeConnection:
            last_call = None

            def __init__(self, host, port, timeout):
                self.host = host
                self.port = port
                self.timeout = timeout

            def request(self, method, path, body=None, headers=None, *, encode_chunked=False):
                FakeConnection.last_call = {
                    "method": method,
                    "path": path,
                    "body": body,
                    "headers": headers,
                }

            def getresponse(self):
                return FakeResponse()

            def close(self):
                return None

        client._auth_token = "test-token"
        client._auth_token_store = "store"
        client._warnings_shown = False
        try:
            with patch("keep.daemon_client.http.client.HTTPConnection", FakeConnection):
                assert client.check_health(5337) is True
        finally:
            client._auth_token = ""
            client._auth_token_store = ""
            client._warnings_shown = False

        assert FakeConnection.last_call == {
            "method": "GET",
            "path": "/v1/ready",
            "body": None,
            "headers": {"Authorization": "Bearer test-token"},
        }


class TestStopDaemon:
    """Daemon stop helper should preserve accurate liveness state."""

    def test_stop_daemon_accepts_string_store_path_and_force_kills(self, tmp_path):
        """String paths should work, and force mode should remove a stuck daemon."""
        from keep.daemon_client import stop_daemon

        store = tmp_path / "store"
        store.mkdir()
        pid_file = store / "processor.pid"
        pid_file.write_text("12345")
        signals = []
        alive = {"value": True}
        times = iter([0.0, 0.0, 0.6, 0.6, 0.6])

        def fake_kill(pid, sig):
            signals.append(sig)
            if sig == 0:
                if alive["value"]:
                    return None
                raise ProcessLookupError
            if sig == signal.SIGKILL:
                alive["value"] = False
            return None

        with (
            patch("keep.daemon_client.os.kill", side_effect=fake_kill),
            patch("keep.daemon_client.time.monotonic", side_effect=lambda: next(times)),
            patch("keep.daemon_client.time.sleep"),
        ):
            assert stop_daemon(str(store), timeout=0.5, force=True) is True

        assert not pid_file.exists()
        assert signals == [signal.SIGTERM, 0, signal.SIGKILL, 0]

    def test_stop_daemon_keeps_pid_file_if_process_survives(self, tmp_path):
        """A live daemon must keep its PID file when shutdown times out."""
        from keep.daemon_client import stop_daemon

        store = tmp_path / "store"
        store.mkdir()
        pid_file = store / "processor.pid"
        pid_file.write_text("12345")
        times = iter([0.0, 0.0, 0.6])

        with (
            patch("keep.daemon_client.os.kill", return_value=None),
            patch("keep.daemon_client.time.monotonic", side_effect=lambda: next(times)),
            patch("keep.daemon_client.time.sleep"),
        ):
            assert stop_daemon(store, timeout=0.5) is False

        assert pid_file.exists()
