"""Tests for the daemon HTTP query server.

Uses mock_providers to avoid loading real ML models.
Tests both raw HTTP and RemoteKeeper round-trip.
"""

import http.client
import json
import socket
from pathlib import Path

import pytest

from keep.api import Keeper
from keep.daemon_server import DaemonServer


@pytest.fixture
def daemon(mock_providers, tmp_path):
    """Start a DaemonServer on an OS-assigned port."""
    kp = Keeper(store_path=tmp_path)
    server = DaemonServer(kp, port=0)
    port = server.start()
    yield server, kp, port
    server.stop()
    kp.close()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = json.loads(resp.read())
    status = resp.status
    conn.close()
    return status, body


def _post(port, path, data):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, json.dumps(data), {"Content-Type": "application/json"})
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read())
    conn.close()
    return status, body


def _patch(port, path, data):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("PATCH", path, json.dumps(data), {"Content-Type": "application/json"})
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read())
    conn.close()
    return status, body


def _delete(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("DELETE", path)
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read())
    conn.close()
    return status, body


# --- Health ---

def test_health(daemon):
    _, _, port = daemon
    status, body = _get(port, "/v1/health")
    assert status == 200
    assert body["status"] == "ok"
    assert "pid" in body
    assert "version" in body
    assert "store" in body
    assert "embedding" in body
    assert "needs_setup" in body
    assert "warnings" in body
    assert isinstance(body["warnings"], list)


def test_404_unknown_path(daemon):
    _, _, port = daemon
    status, body = _get(port, "/v1/nonexistent")
    assert status == 404


# --- Put / Get / Delete ---

def test_put_and_get(daemon):
    _, _, port = daemon
    status, item = _post(port, "/v1/notes", {
        "content": "test note", "id": "test-1", "tags": {"topic": "cache"},
    })
    assert status == 200
    assert item["id"] == "test-1"

    status, item = _get(port, "/v1/notes/test-1")
    assert status == 200
    assert item["id"] == "test-1"

    status, _ = _get(port, "/v1/notes/nonexistent")
    assert status == 404


def test_delete(daemon):
    _, _, port = daemon
    _post(port, "/v1/notes", {"content": "to delete", "id": "del-1"})
    status, body = _delete(port, "/v1/notes/del-1")
    assert status == 200
    assert body["deleted"] is True

    status, _ = _get(port, "/v1/notes/del-1")
    assert status == 404


# --- Tag ---

def test_tag(daemon):
    _, _, port = daemon
    _post(port, "/v1/notes", {"content": "tag test", "id": "tag-1"})
    status, item = _patch(port, "/v1/notes/tag-1/tags", {"set": {"color": "blue"}})
    assert status == 200
    assert item["tags"].get("color") == "blue"


# --- Find ---

def test_find(daemon):
    _, _, port = daemon
    _post(port, "/v1/notes", {"content": "alpha beta", "id": "s-1"})
    status, body = _post(port, "/v1/search", {"query": "alpha", "limit": 5})
    assert status == 200
    assert "notes" in body


# --- Flow ---

def test_flow(daemon):
    _, _, port = daemon
    _post(port, "/v1/notes", {"content": "flow test", "id": "f-1"})
    status, body = _post(port, "/v1/flow", {
        "state": "get",
        "params": {"item_id": "f-1", "similar_limit": 1, "meta_limit": 1,
                   "parts_limit": 0, "edges_limit": 0, "versions_limit": 0},
    })
    assert status == 200
    assert body["status"] == "done"
    assert "bindings" in body


# --- Port fallback ---

def test_port_fallback(mock_providers, tmp_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    occupied_port = sock.getsockname()[1]
    sock.listen(1)
    try:
        kp = Keeper(store_path=tmp_path)
        server = DaemonServer(kp, port=occupied_port)
        actual_port = server.start()
        assert actual_port != occupied_port
        status, _ = _get(actual_port, "/v1/health")
        assert status == 200
        server.stop()
        kp.close()
    finally:
        sock.close()


# --- RemoteKeeper round-trip ---

def test_remote_keeper_round_trip(daemon):
    _, kp, port = daemon
    from keep.remote import RemoteKeeper

    client = RemoteKeeper(
        api_url=f"http://127.0.0.1:{port}", api_key="", config=kp.config)

    # Put
    item = client.put(content="round trip test", id="rt-1", tags={"status": "test"})
    assert item.id == "rt-1"

    # Get
    item = client.get("rt-1")
    assert item is not None
    assert item.id == "rt-1"

    # Find
    results = client.find(query="round trip")
    assert isinstance(results, list)

    # Tag
    tagged = client.tag("rt-1", {"color": "red"})
    assert tagged is not None

    # Exists
    assert client.exists("rt-1")
    assert not client.exists("nonexistent")

    # Delete
    assert client.delete("rt-1") is True

    client.close()


def test_context_endpoint(daemon):
    """The /context endpoint returns full ItemContext in one call."""
    _, _, port = daemon
    _post(port, "/v1/notes", {"content": "context endpoint test", "id": "ce-1"})
    status, body = _get(port, "/v1/notes/ce-1/context?similar_limit=2&edges_limit=1")
    assert status == 200
    assert body["item"]["id"] == "ce-1"
    assert "similar" in body
    assert "meta" in body
    assert "parts" in body
    assert "prev" in body

    # Missing item
    status, _ = _get(port, "/v1/notes/nonexistent/context")
    assert status == 404


def test_remote_keeper_get_context_via_flow(daemon):
    """get_context() uses get + flow endpoint."""
    _, kp, port = daemon
    from keep.remote import RemoteKeeper

    client = RemoteKeeper(
        api_url=f"http://127.0.0.1:{port}", api_key="", config=kp.config)

    client.put(content="context test note", id="ctx-1")
    ctx = client.get_context("ctx-1", edges_limit=2, parts_limit=5)
    assert ctx is not None
    assert ctx.item.id == "ctx-1"
    assert isinstance(ctx.similar, list)
    assert isinstance(ctx.meta, dict)
    assert isinstance(ctx.parts, list)

    # Missing item
    ctx = client.get_context("nonexistent")
    assert ctx is None

    client.close()


# --- Prompt via flow ---

def test_prompt_via_flow(daemon):
    _, kp, port = daemon
    kp.put(content="# Test\nA test.\n\n## Prompt\nHello {get}", id=".prompt/agent/test-render")
    status, body = _post(port, "/v1/flow", {
        "state": "prompt", "params": {"name": "test-render"},
    })
    assert status == 200
    assert body["status"] == "done"
    assert "text" in body.get("data", {})
    assert len(body["data"]["text"]) > 0


def test_prompt_not_found_via_flow(daemon):
    _, _, port = daemon
    status, body = _post(port, "/v1/flow", {
        "state": "prompt", "params": {"name": "nonexistent-prompt"},
    })
    assert status == 200
    assert body["status"] == "error"
