from core.event import ensure_event
from core.flows import execute_flow, validate_flow
from core.persistence import SQLitePhase9Store


def make_store(tmp_path):
    return SQLitePhase9Store(tmp_path / "flows.db")


def test_flow_execution_fanout_and_persisted_readback(tmp_path):
    store = make_store(tmp_path)
    store.upsert_destination("out", "Outbound", "https://example.com/hook", enabled=True)
    flow = {
        "id": "f", "name": "Flow", "enabled": True,
        "nodes": [
            {"id": "trigger", "type": "endpoint_trigger", "endpoint_id": "in"},
            {"id": "transform", "type": "transform", "template": "{{ event }}"},
            {"id": "left", "type": "webhook_outbound", "endpoint_id": "out"},
            {"id": "right", "type": "webhook_outbound", "endpoint_id": "out"},
        ],
        "edges": [["trigger", "transform"], ["transform", "left"], ["transform", "right"]],
    }
    assert validate_flow(flow)["valid"]
    event = ensure_event({"source": {"type": "webhook"}, "event": {"type": "test"}, "content": {"text": "hello"}})
    run_id = execute_flow(store, flow, event, "in")
    run = store.get_flow_run(run_id)
    assert run["status"] == "success"
    assert [n["node_id"] for n in run["node_runs"]] == ["trigger", "transform", "left", "right"]
    deliveries = store.list_deliveries()
    assert len(deliveries) == 2
    assert {d.flow_run_id for d in deliveries} == {run_id}
    assert {d.node_id for d in deliveries} == {"left", "right"}


def test_flow_persists_failed_node_and_run(tmp_path):
    store = make_store(tmp_path)
    flow = {
        "id": "f", "name": "Flow", "enabled": True,
        "nodes": [
            {"id": "trigger", "type": "endpoint_trigger", "endpoint_id": "in"},
            {"id": "transform", "type": "transform", "template": "{{ missing.value }}"},
        ], "edges": [["trigger", "transform"]],
    }
    event = ensure_event({"source": {"type": "webhook"}, "event": {"type": "test"}, "content": {"text": "hello"}})
    run = store.get_flow_run(execute_flow(store, flow, event, "in"))
    assert run["status"] == "failed"
    assert run["node_runs"][-1]["status"] == "failed"
