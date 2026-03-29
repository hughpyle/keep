"""Tests for the flow-host primary interface.

Unit 1 establishes ``run_flow`` as the stable execution boundary and
routes public memory helpers through shared flow-backed wrappers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from keep.api import Keeper
from keep.config import StoreConfig
from keep.flow_client import (
    FLOW_STATE_DELETE_ITEM,
    FLOW_STATE_FIND_ITEMS,
    FLOW_STATE_GET_ITEM,
    FLOW_STATE_GET_NOW,
    FLOW_STATE_PUT_ITEM,
    FLOW_STATE_TAG_ITEM,
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
    assert calls == [FLOW_STATE_PUT_ITEM]

    calls.clear()
    fetched = kp.get("fh-1")
    assert fetched is not None
    assert fetched.id == "fh-1"
    assert calls == [FLOW_STATE_GET_ITEM]

    calls.clear()
    results = kp.find(query="Flow-host")
    assert any(item.id == "fh-1" for item in results)
    assert calls == [FLOW_STATE_FIND_ITEMS]

    calls.clear()
    tagged = kp.tag("fh-1", {"status": "open"})
    assert tagged is not None
    assert tagged.tags.get("status") == "open"
    assert calls == [FLOW_STATE_TAG_ITEM]

    calls.clear()
    now_item = kp.get_now()
    assert now_item.id == "now"
    assert calls == [FLOW_STATE_GET_NOW]

    calls.clear()
    deleted = kp.delete("fh-1")
    assert deleted is True
    assert calls == [FLOW_STATE_DELETE_ITEM]


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
        if state == FLOW_STATE_GET_ITEM:
            item_id = kwargs.get("params", {}).get("id", "fh-remote")
            if item_id == "missing":
                return FlowResult(status="done", data={"item": None}, ticks=1)
            return FlowResult(
                status="done",
                data={"item": {"id": item_id, "summary": "remote item", "tags": {"kind": "test"}}},
                ticks=1,
            )
        if state == FLOW_STATE_PUT_ITEM:
            params = kwargs.get("params", {})
            return FlowResult(
                status="done",
                data={"item": {"id": params.get("id", "fh-remote"), "summary": params.get("content", ""), "tags": params.get("tags", {}) or {}}},
                ticks=1,
            )
        if state == FLOW_STATE_FIND_ITEMS:
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
        if state == FLOW_STATE_TAG_ITEM:
            params = kwargs.get("params", {})
            return FlowResult(
                status="done",
                data={"item": {"id": params.get("id", "fh-remote"), "summary": "remote item", "tags": params.get("tags", {}) or {}}},
                ticks=1,
            )
        if state == FLOW_STATE_DELETE_ITEM:
            return FlowResult(status="done", data={"deleted": True}, ticks=1)
        if state == FLOW_STATE_GET_NOW:
            return FlowResult(
                status="done",
                data={"item": {"id": "now", "summary": "working context", "tags": {}}},
                ticks=1,
            )
        raise AssertionError(f"unexpected flow state: {state}")

    client.run_flow = fake_run_flow  # type: ignore[method-assign]
    try:
        item = client.put(content="remote text", id="fh-remote", tags={"topic": "flows"})
        assert item.id == "fh-remote"
        assert calls == [FLOW_STATE_PUT_ITEM]

        calls.clear()
        fetched = client.get("fh-remote")
        assert fetched is not None
        assert fetched.id == "fh-remote"
        assert calls == [FLOW_STATE_GET_ITEM]

        calls.clear()
        assert client.get("missing") is None
        assert calls == [FLOW_STATE_GET_ITEM]

        calls.clear()
        results = client.find(query="remote")
        assert [item.id for item in results] == ["fh-remote"]
        assert calls == [FLOW_STATE_FIND_ITEMS]

        calls.clear()
        tagged = client.tag("fh-remote", {"status": "open"})
        assert tagged is not None
        assert tagged.tags == {"status": "open"}
        assert calls == [FLOW_STATE_TAG_ITEM]

        calls.clear()
        now_item = client.get_now()
        assert now_item.id == "now"
        assert calls == [FLOW_STATE_GET_NOW]

        calls.clear()
        assert client.delete("fh-remote") is True
        assert calls == [FLOW_STATE_DELETE_ITEM]
    finally:
        client.close()
