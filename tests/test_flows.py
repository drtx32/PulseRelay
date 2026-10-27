from core.event import ensure_event
from core.flows import execute_flow, validate_flow
from core.persistence import SQLitePhase9Store
from core.mcp_auth import Principal, current_principal, normalize_scopes
from core.control_plane import ControlPlaneService


def make_store(tmp_path):
    return SQLitePhase9Store(tmp_path / "flows.db")


def test_flow_execution_fanout_and_persisted_readback(tmp_path):
    store = make_store(tmp_path)
    store.upsert_destination("out", "Outbound", "https://example.com/hook", enabled=True)
    flow = {
        "id": "f", "name": "Flow", "enabled": True,
        "nodes": [
            {"id": "trigger", "type": "endpoint_trigger", "endpoint_id": "in"},
            {"id": "filter", "type": "filter", "expression": {"event.type": "test"}},
            {"id": "transform", "type": "transform", "template": "{{ event }}"},
            {"id": "left", "type": "webhook_outbound", "endpoint_id": "out"},
            {"id": "right", "type": "webhook_outbound", "endpoint_id": "out"},
        ],
        "edges": [["trigger", "filter"], ["filter", "transform"], ["transform", "left"], ["transform", "right"]],
    }
    assert validate_flow(flow)["valid"]
    event = ensure_event({"source": {"type": "webhook"}, "event": {"type": "test"}, "content": {"text": "hello"}})
    run_id = execute_flow(store, flow, event, "in")
    run = store.get_flow_run(run_id)
    assert run["status"] == "success"
    assert [n["node_id"] for n in run["node_runs"]] == ["trigger", "filter", "transform", "left", "right"]
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


def test_flow_mcp_scopes_admin_mutation_readback_and_redaction(tmp_path, monkeypatch):
    import mcp_control
    store = make_store(tmp_path)
    store.create_inbound_endpoint("in", "Inbound", "hash")
    store.upsert_destination("out", "Outbound", "https://example.com/hook")
    monkeypatch.setattr(mcp_control.control, "store", store)
    flow = {"id": "secured", "name": "Secured", "enabled": True,
            "nodes": [{"id": "t", "type": "endpoint_trigger", "endpoint_id": "in"},
                      {"id": "f", "type": "filter", "expression": {"api_key": "top-secret"}}],
            "edges": [["t", "f"]]}
    reader = current_principal.set(Principal("reader", "test", frozenset(normalize_scopes("routes:read deliveries:read"))))
    try:
        assert mcp_control.create_flow(flow)["error"] == "missing scope: routes:write"
    finally:
        current_principal.reset(reader)
    admin = current_principal.set(Principal("admin", "test", frozenset(normalize_scopes("admin"))))
    try:
        assert mcp_control.create_flow(flow)["ok"]
        assert mcp_control.update_flow("secured", {"name": "Updated"})["ok"]
        assert mcp_control.disable_flow("secured")["ok"]
        returned = mcp_control.get_flow("secured")["flow"]
        assert returned["nodes"][1]["expression"]["api_key"] != "top-secret"
        event = ensure_event({"source": {"type": "webhook"}, "event": {"type": "test"}, "content": {"text": "hello"}})
        from core.relay import DurableRelay
        relay = DurableRelay(store)
        relay.ingest(event, endpoint_id="in")
        assert store.list_flow_runs() == []
        store.set_flow_enabled("secured", True)
        flow_run_id = execute_flow(store, store.get_flow("secured"), event, "in")
        assert mcp_control.get_flow_run(flow_run_id)["flow_run"]["node_runs"]
        store.create_node_run(flow_run_id, "secret-node", "success", {}, {"api_key": "top-secret"})
        assert "top-secret" not in str(ControlPlaneService(store).get_flow_run(flow_run_id))
        store.create_delivery("secret-delivery", "out", "secret-key", event_id=event.id)
        store.update_delivery("secret-delivery", "failed", error="Authorization: Bearer top-secret")
        assert "top-secret" not in str(ControlPlaneService(store).get_delivery("secret-delivery"))
    finally:
        current_principal.reset(admin)


def test_disabled_outbound_connector_fails_closed(tmp_path):
    store = make_store(tmp_path)
    store.upsert_destination("out", "Outbound", "https://example.com/hook", enabled=False)
    flow = {"id": "f", "name": "Flow", "enabled": True,
            "nodes": [{"id": "t", "type": "endpoint_trigger", "endpoint_id": "in"},
                      {"id": "o", "type": "webhook_outbound", "endpoint_id": "out"}],
            "edges": [["t", "o"]]}
    event = ensure_event({"source": {"type": "webhook"}, "event": {"type": "test"}, "content": {"text": "hello"}})
    run = store.get_flow_run(execute_flow(store, flow, event, "in"))
    assert run["status"] == "failed"
    assert run["node_runs"][-1]["status"] == "failed"
    assert store.list_deliveries() == []
