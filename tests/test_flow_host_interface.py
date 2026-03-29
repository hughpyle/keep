"""Tests for the flow-host primary interface.

Units 1 and 2 establish ``run_flow`` as the stable execution boundary,
with existing named state docs used where they already cover behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keep.api import Keeper
from keep.config import StoreConfig
from keep.flow_client import (
    STATE_DELETE,
    STATE_PUT,
    STATE_TAG,
)
from keep.protocol import FlowHostProtocol
from keep.remote import RemoteKeeper
from keep.state_doc_runtime import FlowResult


@pytest.fixture
def kp(mock_providers, tmp_path: Path):
    keeper = Keeper(store_path=tmp_path)
    keeper._get_embedding_provider()
    return keeper


def test_keeper_implements_flow_host_protocol(kp):
    assert isinstance(kp, FlowHostProtocol)


def test_keeper_public_memory_methods_delegate_via_run_flow(kp):
    kp._ensure_sysdocs()
    calls: list[str] = []
    original = kp.run_flow

    def tracking(state, **kwargs):
        calls.append(state)
        return original(state, **kwargs)

    kp.run_flow = tracking  # type: ignore[method-assign]

    item = kp.put("Flow-host put", id="fh-1", tags={"topic": "flows"})
    assert item.id == "fh-1"
    assert calls == [STATE_PUT]

    calls.clear()
    fetched = kp.get("fh-1")
    assert fetched is not None
    assert fetched.id == "fh-1"
    assert calls == ["compat-get-item"]

    calls.clear()
    results = kp.find(query="Flow-host")
    assert any(item.id == "fh-1" for item in results)
    assert calls == ["compat-find"]

    calls.clear()
    tagged = kp.tag("fh-1", {"status": "open"})
    assert tagged is not None
    assert tagged.tags.get("status") == "open"
    assert calls == [STATE_TAG, "compat-get-item"]

    calls.clear()
    now_item = kp.get_now()
    assert now_item.id == "now"
    assert calls == ["compat-get-item", STATE_PUT]

    calls.clear()
    deleted = kp.delete("fh-1")
    assert deleted is True
    assert calls == [STATE_DELETE]


def test_named_writable_flows_apply_effects(kp):
    result = kp.run_flow("put", params={"content": "via flow", "id": "flow-note"})
    assert result.status == "done"
    assert kp.get("flow-note") is not None

    result = kp.run_flow("tag", params={"id": "flow-note", "tags": {"status": "open"}})
    assert result.status == "done"
    tagged = kp.get("flow-note")
    assert tagged is not None
    assert tagged.tags.get("status") == "open"

    result = kp.run_flow("delete", params={"id": "flow-note"})
    assert result.status == "done"
    assert kp.get("flow-note") is None


def test_remote_keeper_implements_flow_host_protocol(tmp_path: Path):
    client = RemoteKeeper(
        api_url="http://127.0.0.1:1",
        api_key="",
        config=StoreConfig(path=tmp_path),
    )
    try:
        assert isinstance(client, FlowHostProtocol)
    finally:
        client.close()


def test_remote_keeper_public_memory_methods_delegate_via_run_flow(tmp_path: Path):
    client = RemoteKeeper(
        api_url="http://127.0.0.1:1",
        api_key="",
        config=StoreConfig(path=tmp_path),
    )
    calls: list[str] = []

    def fake_run_flow(state, **kwargs):
        calls.append(state)
        if state == "compat-get-item":
            item_id = kwargs.get("params", {}).get("id", "fh-remote")
            if item_id == "missing":
                return FlowResult(status="done", data={"item": None}, ticks=1)
            return FlowResult(
                status="done",
                data={"item": {"id": item_id, "summary": "remote item", "tags": {"kind": "test"}}},
                ticks=1,
            )
        if state == STATE_PUT:
            params = kwargs.get("params", {})
            return FlowResult(
                status="done",
                bindings={"stored": {"id": params.get("id", "fh-remote"), "summary": params.get("content", ""), "tags": params.get("tags", {}) or {}}},
                ticks=1,
            )
        if state == "compat-find":
            return FlowResult(
                status="done",
                data={
                    "items": [
                        {"id": "fh-remote", "summary": "remote item", "tags": {"kind": "test"}, "score": 0.9},
                    ],
                    "deep_groups": {},
                },
                ticks=1,
            )
        if state == STATE_TAG:
            return FlowResult(status="done", bindings={"tagged": {"count": 1, "ids": ["fh-remote"]}}, ticks=1)
        if state == STATE_DELETE:
            return FlowResult(status="done", data={"deleted": True}, ticks=1)
        raise AssertionError(f"unexpected flow state: {state}")

    client.run_flow = fake_run_flow  # type: ignore[method-assign]
    try:
        item = client.put(content="remote text", id="fh-remote", tags={"topic": "flows"})
        assert item.id == "fh-remote"
        assert calls == [STATE_PUT]

        calls.clear()
        fetched = client.get("fh-remote")
        assert fetched is not None
        assert fetched.id == "fh-remote"
        assert calls == ["compat-get-item"]

        calls.clear()
        assert client.get("missing") is None
        assert calls == ["compat-get-item"]

        calls.clear()
        results = client.find(query="remote")
        assert [item.id for item in results] == ["fh-remote"]
        assert calls == ["compat-find"]

        calls.clear()
        tagged = client.tag("fh-remote", {"status": "open"})
        assert tagged is not None
        assert tagged.tags == {"kind": "test"}
        assert calls == [STATE_TAG, "compat-get-item"]

        calls.clear()
        now_item = client.get_now()
        assert now_item.id == "now"
        assert calls == ["compat-get-item"]

        calls.clear()
        assert client.delete("fh-remote") is True
        assert calls == [STATE_DELETE]
    finally:
        client.close()
