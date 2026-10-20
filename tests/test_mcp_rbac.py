from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from fastapi.testclient import TestClient
from core.control_plane import ControlPlaneService
from core.event import ensure_event
from core.mcp_auth import MCPAuthStore, Principal, current_principal, normalize_scopes
from core.persistence import SQLitePhase9Store
from core.relay import DurableRelay
import core.webhook_delivery as webhook_delivery
import mcp_control


def test_mcp_http_bearer_and_oauth_tokens_are_independently_authenticated(tmp_path, monkeypatch):
    auth = MCPAuthStore(tmp_path / "mcp-http-auth.db")
    monkeypatch.setattr(mcp_control, "auth_store", auth)
    store = SQLitePhase9Store(tmp_path / "mcp-http-store.db")
    store.upsert_destination("fixture-destination", "Fixture", "https://receiver.example/hook")
    monkeypatch.setattr(mcp_control, "store", store)
    monkeypatch.setattr(mcp_control, "control", ControlPlaneService(store))
    api_key = auth.create_api_key("bearer-reader")
    oauth_client = auth.create_oauth_client("oauth-reader")
    oauth_token = auth.issue_client_credentials_token(oauth_client["client_id"], oauth_client["client_secret"])
    scoped_admin = auth.create_api_key("scoped-admin", "routes:read routes:write")
    client = TestClient(mcp_control.app, base_url="http://127.0.0.1:8000")
    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "rbac-test", "version": "1"}},
    }
    with client:
        assert client.post("/mcp", json=initialize).status_code == 401
        for index, (secret, may_write) in enumerate((
            (api_key["api_key"], False),
            (oauth_token["access_token"], False),
            (scoped_admin["api_key"], True),
        ), start=1):
            headers = {"Authorization": f"Bearer {secret}"}
            response = client.post("/mcp", headers=headers, json=initialize)
            assert response.status_code == 200, response.text
            headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            headers["Mcp-Protocol-Version"] = "2024-11-05"
            called = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": index + 1, "method": "tools/call",
                "params": {"name": "create_or_update_route", "arguments": {
                    "route_id": f"mcp-rbac-{index}", "name": "RBAC", "match": {},
                    "destinations": ["fixture-destination"],
                }},
            })
            assert called.status_code == 200, called.text
            result = called.json()["result"]["structuredContent"]
            if may_write:
                assert result["ok"]
                assert any(route.id == f"mcp-rbac-{index}" for route in store.list_routes())
            else:
                assert result["error"] == "missing scope: routes:write"
                assert not any(route.id == f"mcp-rbac-{index}" for route in store.list_routes())


def test_read_only_mcp_cannot_mutate_and_admin_can_build_filter_transform_route(tmp_path, monkeypatch):
    store = SQLitePhase9Store(tmp_path / "mcp-rbac.db")
    control = ControlPlaneService(store)
    monkeypatch.setattr(mcp_control, "store", store)
    monkeypatch.setattr(mcp_control, "control", control)

    reader = Principal("agent", "oauth", frozenset(normalize_scopes("read")))
    token = current_principal.set(reader)
    try:
        assert mcp_control.list_connectors()["ok"]
        assert mcp_control.list_routes()["ok"]
        assert mcp_control.list_events()["ok"]
        assert mcp_control.create_webhook_inbound("in-a", "Inbound A")["error"] == "missing scope: connectors:write"
        assert mcp_control.rotate_webhook_inbound_secret("in-a")["error"] == "missing scope: connectors:rotate_secret"
        assert mcp_control.create_or_update_route("r", "Route", match={}, destinations=["out-b"])["error"] == "missing scope: routes:write"
    finally:
        current_principal.reset(token)

    admin = Principal("route-admin", "oauth", frozenset(normalize_scopes("connectors:read connectors:write routes:read routes:write")))
    token = current_principal.set(admin)
    try:
        denied_private = mcp_control.create_webhook_outbound(
            "private-denied", "Private target", "http://127.0.0.1:1/hook", config={"allow_private": True}
        )
        assert denied_private["error"] == "missing scope: webhooks:private_network"
    finally:
        current_principal.reset(token)

    received = {}

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received["body"] = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(202)
            self.end_headers()

        def log_message(self, _format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    admin = Principal("route-admin", "oauth", frozenset(normalize_scopes(
        "connectors:read connectors:write routes:read routes:write webhooks:private_network")))
    token = current_principal.set(admin)
    try:
        inbound = mcp_control.create_webhook_inbound("in-a", "Inbound A", source_type="source-a")
        assert inbound["ok"] and inbound["connector"]["secret"]
        outbound = mcp_control.create_webhook_outbound(
            "out-b", "Outbound B", f"http://127.0.0.1:{server.server_port}/webhook",
            config={"message_type": "json", "message_template": '{"normalized":"${input content}"}',
                    "headers": {"Authorization": "Bearer outbound-auth-secret"}, "allow_private": True},
        )
        assert outbound["ok"]
        assert outbound["connector"]["config"]["headers"]["Authorization"] == "<redacted>"
        assert "secret" not in mcp_control.get_connector("in-a")["connector"]
        route = mcp_control.create_or_update_route(
            "route-a-b", "A filter transform B",
            match={"source.type": "source-a", "event.type": "customer.created"},
            destinations=["out-b"], enabled=True,
        )
        assert route["ok"] and route["route"]["enabled"]
        relay = DurableRelay(store)
        relay.ingest(ensure_event({
            "id": "event-1", "source": {"type": "source-a", "id": "in-a"},
            "event": {"type": "customer.created", "dedupe_key": "event-1"},
            "content": {"text": "safe payload"},
        }))
        assert len(store.list_deliveries()) == 1
        assert asyncio.run(relay.process_one()).status == "success"
        assert json.loads(received["body"]) == {"normalized": "safe payload"}
    finally:
        current_principal.reset(token)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_explicitly_gated_sensitive_mcp_actions(tmp_path, monkeypatch):
    store = SQLitePhase9Store(tmp_path / "mcp-sensitive.db")
    monkeypatch.setattr(mcp_control, "store", store)
    monkeypatch.setattr(mcp_control, "control", ControlPlaneService(store))
    admin_without_sensitive_scopes = Principal("admin", "oauth", frozenset({"admin"}))
    token = current_principal.set(admin_without_sensitive_scopes)
    try:
        assert mcp_control.rotate_webhook_inbound_secret("missing")["error"] == "missing scope: connectors:rotate_secret"
    finally:
        current_principal.reset(token)


def test_mcp_webhook_edit_preserves_redactions_and_reauthorizes_private_retarget(tmp_path, monkeypatch):
    store = SQLitePhase9Store(tmp_path / "mcp-private-retarget.db")
    monkeypatch.setattr(mcp_control, "store", store)
    monkeypatch.setattr(mcp_control, "control", ControlPlaneService(store))
    private_admin = Principal("private-admin", "oauth", frozenset(normalize_scopes(
        "connectors:read connectors:write webhooks:private_network")))
    token = current_principal.set(private_admin)
    try:
        created = mcp_control.create_webhook_outbound(
            "out", "Outbound", "https://receiver.example/hooks/out/0123456789abcdefghijklmnopqrstuv?token=url-secret",
            config={"allow_private": True, "headers": {"Authorization": "Bearer header-secret"},
                    "hmac_secret": "signing-secret", "message_type": "json"},
        )
        assert created["ok"]
        public = mcp_control.get_connector("out")["connector"]
        assert "url-secret" not in public["url"]
        assert public["config"]["headers"]["Authorization"] == "<redacted>"
        assert public["config"]["hmac_secret"] == "<redacted>"
        edited = mcp_control.update_webhook_outbound(
            "out", name="renamed", url=public["url"], config=public["config"]
        )
        assert edited["ok"]
        saved = store.get_destination("out")
        assert saved.name == "renamed"
        assert saved.url == "https://receiver.example/hooks/out/0123456789abcdefghijklmnopqrstuv?token=url-secret"
        assert saved.config["headers"]["Authorization"] == "Bearer header-secret"
        assert saved.config["hmac_secret"] == "signing-secret"
    finally:
        current_principal.reset(token)

    scoped_admin = Principal("scoped-admin", "oauth", frozenset(normalize_scopes(
        "connectors:read connectors:write")))
    token = current_principal.set(scoped_admin)
    try:
        denied = mcp_control.update_webhook_outbound(
            "out", url="http://127.0.0.1:8088/new-hook"
        )
        assert denied["error"] == "missing scope: webhooks:private_network"
    finally:
        current_principal.reset(token)
    reader = Principal("reader", "oauth", frozenset(normalize_scopes("read")))
    token = current_principal.set(reader)
    try:
        denied_write = mcp_control.update_webhook_outbound("out", name="forbidden")
        assert denied_write["error"] == "missing scope: connectors:write"
    finally:
        current_principal.reset(token)
