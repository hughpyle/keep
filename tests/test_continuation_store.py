import json

from keep.continuation_store import SQLiteFlowStore


def test_sqlite_flow_store_transaction_rollback(tmp_path):
    store = SQLiteFlowStore(tmp_path / "continuation.db")
    try:
        store.begin_immediate()
        flow = store.create_flow(json.dumps({"cursor": {"step": 0}}))
        store.rollback()
        assert store.get_flow(flow.flow_id) is None
    finally:
        store.close()


def test_sqlite_flow_store_flow_work_and_idempotency(tmp_path):
    store = SQLiteFlowStore(tmp_path / "continuation.db")
    try:
        store.begin_immediate()
        flow = store.create_flow(json.dumps({"cursor": {"step": 0}}))
        work_id = store.insert_work(
            flow_id=flow.flow_id,
            kind="summarize",
            input_json=json.dumps({"item_id": "note:1", "content": "alpha"}),
            output_contract_json=json.dumps({"must_return": ["summary"]}),
        )
        store.update_work_result(
            work_id=work_id,
            status="completed",
            result_json=json.dumps({"outputs": {"summary": "alpha"}}),
        )
        store.update_flow(
            flow.flow_id,
            state_version=1,
            status="done",
            state_json=json.dumps({"cursor": {"step": 1}}),
        )
        store.store_idempotent("idem-1", "hash-1", json.dumps({"status": "done"}))
        store.commit()

        loaded = store.get_flow(flow.flow_id)
        assert loaded is not None
        assert loaded.state_version == 1
        assert loaded.status == "done"
        assert store.has_any_work_key(flow.flow_id, "summarize") is True
        assert store.has_completed_work_key(flow.flow_id, "summarize") is True
        idem = store.load_idempotent("idem-1")
        assert idem == ("hash-1", json.dumps({"status": "done"}))
    finally:
        store.close()


def test_sqlite_flow_store_mutation_queue(tmp_path):
    store = SQLiteFlowStore(tmp_path / "continuation.db")
    try:
        store.begin_immediate()
        flow = store.create_flow(json.dumps({"cursor": {"step": 0}}))
        op_json = json.dumps(
            {"op": "set_tags", "target": "note:1", "tags": {"topic": "x"}},
            sort_keys=True,
            separators=(",", ":"),
        )
        mutation_id = store.insert_pending_mutation(
            flow_id=flow.flow_id,
            work_id="w_1",
            op_json=op_json,
        )
        store.commit()

        pending = store.list_pending_mutations(flow_id=flow.flow_id)
        assert [m.mutation_id for m in pending] == [mutation_id]
        store.set_mutation_status(mutation_id, status="applied")
        assert store.list_pending_mutations(flow_id=flow.flow_id) == []
    finally:
        store.close()
