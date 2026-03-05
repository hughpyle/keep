from keep.api import Keeper
from keep.continuation_engine import ContinuationEngine
from keep.continuation_env import LocalContinuationEnvironment
from keep.continuation_store import SQLiteFlowStore


def _summarize_request(note_id: str, content: str, *, request_id: str, idempotency_key: str | None = None) -> dict:
    payload = {
        "request_id": request_id,
        "goal": "summarize",
        "params": {"id": note_id, "content": content},
        "steps": [
            {
                "kind": "summarize",
                "runner": {"type": "provider.summarize"},
                "input_mode": "note_content",
                "output_contract": {"must_return": ["summary"]},
                "apply": {
                    "ops": [
                        {"op": "set_summary", "summary": "$output.summary"},
                    ]
                },
            }
        ],
        "work_results": [],
    }
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return payload


def test_engine_round_trip_with_adapters(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    flow_store = SQLiteFlowStore(tmp_path / "engine-continuation.db")
    engine = ContinuationEngine(
        flow_store=flow_store,
        env=LocalContinuationEnvironment(kp),
    )
    try:
        content = "engine alpha " * 120
        kp.put(content=content, id="note:engine:1", summary="placeholder")

        first = engine.continue_flow(
            _summarize_request("note:engine:1", content, request_id="engine-req-1")
        )
        assert first["status"] == "waiting_work"
        work_id = first["work"][0]["work_id"]

        work_result = engine.run_work(first["cursor"], work_id)
        second = engine.continue_flow(
            {
                "request_id": "engine-req-2",
                "cursor": first["cursor"],
                "work_results": [work_result],
            }
        )
        assert second["status"] == "done"
        item = kp.get("note:engine:1")
        assert item is not None
        assert item.summary == content[:200]
    finally:
        engine.close()
        kp.close()


def test_engine_idempotency_replay_with_adapters(mock_providers, tmp_path):
    kp = Keeper(store_path=tmp_path)
    flow_store = SQLiteFlowStore(tmp_path / "engine-continuation.db")
    engine = ContinuationEngine(
        flow_store=flow_store,
        env=LocalContinuationEnvironment(kp),
    )
    try:
        content = "engine beta " * 80
        kp.put(content=content, id="note:engine:2", summary="placeholder")

        first = engine.continue_flow(
            _summarize_request(
                "note:engine:2",
                content,
                request_id="engine-idem-1",
                idempotency_key="engine-idem-key",
            )
        )
        replay = engine.continue_flow(
            _summarize_request(
                "note:engine:2",
                content,
                request_id="engine-idem-2",
                idempotency_key="engine-idem-key",
            )
        )
        assert replay["cursor"] == first["cursor"]
        assert replay["status"] == first["status"]
    finally:
        engine.close()
        kp.close()
