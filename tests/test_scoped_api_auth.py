from __future__ import annotations

from fastapi.testclient import TestClient

from api import create_app
from core.persistence import SQLitePhase9Store


def test_scoped_bearer_api_reads_and_denies_mutations_and_sensitive_actions(tmp_path):
    store = SQLitePhase9Store(tmp_path / "scoped-api.db")
    app = create_app(store)
    client = TestClient(app)
    auth = app.state.mcp_auth_store
    reader = auth.create_api_key("read-agent")
    ingest = auth.create_api_key("ingest-only", "events:write")
    route_admin = auth.create_api_key(
        "route-admin", "connectors:read connectors:write routes:read routes:write events:read deliveries:read health:read"
    )
    reader_headers = {"Authorization": f"Bearer {reader['api_key']}"}
    ingest_headers = {"Authorization": f"Bearer {ingest['api_key']}"}
    admin_headers = {"Authorization": f"Bearer {route_admin['api_key']}"}

    assert client.get("/v1/hooks").status_code == 401
    assert client.get("/v1/hooks", headers=reader_headers).status_code == 200
    assert client.get("/v1/routes", headers=reader_headers).status_code == 200
    assert client.post("/v1/events", json={"id": "no-auth"}).status_code == 401
    assert client.post("/v1/events", json={"id": "read-only"}, headers=reader_headers).status_code == 403
    accepted = client.post("/v1/events", json={"id": "ingested", "event": {"type": "test"}}, headers=ingest_headers)
    assert accepted.status_code == 200
    assert client.post("/v1/routes", json={"id": "ingest-cannot-configure"}, headers=ingest_headers).status_code == 403
    assert client.post("/v1/hooks", json={"id": "denied"}, headers=reader_headers).status_code == 403
    assert client.post("/v1/routes", json={"id": "denied"}, headers=reader_headers).status_code == 403
    assert client.post("/v1/deliveries/process", headers=reader_headers).status_code == 403

    inbound = client.post("/v1/hooks", json={"id": "source-a", "source_type": "source-a"}, headers=admin_headers)
    assert inbound.status_code == 200
    outbound = client.post("/v1/webhooks", json={
        "id": "out-b",
        "url": "https://receiver.example/hooks/out-b/0123456789abcdefghijklmnopqrstuv?token=outbound-secret",
        "config": {"headers": {"Authorization": "Bearer destination-secret"}},
    }, headers=admin_headers)
    assert outbound.status_code == 200
    assert outbound.json()["webhook"]["url"] == "https://receiver.example/hooks/out-b/<redacted>?token=%3Credacted%3E"
    assert outbound.json()["webhook"]["config"]["headers"]["Authorization"] == "<redacted>"
    private = client.post("/v1/webhooks", json={
        "id": "private-target", "url": "http://127.0.0.1:8080/hook", "config": {"allow_private": True},
    }, headers=admin_headers)
    assert private.status_code == 403
    route = client.post("/v1/routes", json={
        "id": "route-a-b", "name": "A to B", "match": {"source.type": "source-a", "event.type": "customer.created"},
        "destinations": ["out-b"], "enabled": True,
    }, headers=admin_headers)
    assert route.status_code == 200, route.text
    assert client.get("/v1/routes", headers=reader_headers).json()["routes"][0]["id"] == "route-a-b"
    assert client.delete("/v1/routes/route-a-b", headers=admin_headers).status_code == 403
    assert client.delete("/v1/webhooks/out-b", headers=admin_headers).status_code == 403
    assert client.delete("/v1/hooks/source-a", headers=admin_headers).status_code == 403
    assert client.put("/v1/hooks/source-a", json={"rotate_secret": True}, headers=admin_headers).status_code == 403
    assert client.post("/v1/webhooks/out-b/test", json={}, headers=admin_headers).status_code == 403


def test_oauth_bearer_token_uses_same_scopes_as_api_key(tmp_path):
    store = SQLitePhase9Store(tmp_path / "scoped-oauth-api.db")
    app = create_app(store)
    client = TestClient(app)
    auth = app.state.mcp_auth_store
    oauth_client = auth.create_oauth_client("read-agent")
    token = auth.issue_client_credentials_token(oauth_client["client_id"], oauth_client["client_secret"])
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get("/v1/routes", headers=headers).status_code == 200
    assert client.post("/v1/routes", json={"id": "denied"}, headers=headers).status_code == 403


def test_webhook_edit_preserves_redacted_secrets_and_reauthorizes_private_retarget(tmp_path):
    store = SQLitePhase9Store(tmp_path / "webhook-edit-rbac.db")
    app = create_app(store)
    client = TestClient(app)
    auth = app.state.mcp_auth_store
    scoped_admin = auth.create_api_key("scoped-admin", "connectors:read connectors:write")
    private_admin = auth.create_api_key(
        "private-admin", "connectors:read connectors:write webhooks:private_network"
    )
    scoped_headers = {"Authorization": f"Bearer {scoped_admin['api_key']}"}
    private_headers = {"Authorization": f"Bearer {private_admin['api_key']}"}
    original_url = "https://receiver.example/hooks/out/0123456789abcdefghijklmnopqrstuv?token=url-secret"
    original_config = {
        "allow_private": True,
        "headers": {"Authorization": "Bearer header-secret"},
        "hmac_secret": "signing-secret",
        "message_type": "json",
    }
    created = client.post("/v1/webhooks", json={
        "id": "out", "url": original_url, "config": original_config,
    }, headers=private_headers)
    assert created.status_code == 200, created.text
    public_read = client.get("/v1/webhooks/out", headers=scoped_headers).json()["webhook"]
    assert "url-secret" not in public_read["url"]
    assert public_read["config"]["headers"]["Authorization"] == "<redacted>"
    assert public_read["config"]["hmac_secret"] == "<redacted>"

    # Editing only the name while echoing the public projection must not
    # replace stored credentials or the secret-bearing URL with markers.
    public_read["name"] = "renamed"
    edited = client.put("/v1/webhooks/out", json=public_read, headers=scoped_headers)
    assert edited.status_code == 200, edited.text
    saved = store.get_destination("out")
    assert saved.name == "renamed"
    assert saved.url == original_url
    assert saved.config == original_config

    # Existing private-network permission does not transfer to a new target.
    retargeted = client.put("/v1/webhooks/out", json={
        "url": "http://127.0.0.1:8088/new-hook",
        "config": public_read["config"],
    }, headers=scoped_headers)
    assert retargeted.status_code == 403
    assert store.get_destination("out").url == original_url
    accepted = client.put("/v1/webhooks/out", json={
        "url": "http://127.0.0.1:8088/new-hook",
    }, headers=private_headers)
    assert accepted.status_code == 200, accepted.text
    assert store.get_destination("out").url == "http://127.0.0.1:8088/new-hook"


def test_oauth_scoped_admin_is_forbidden_to_retarget_private_webhook_without_scope(tmp_path):
    store = SQLitePhase9Store(tmp_path / "oauth-private-retarget.db")
    app = create_app(store)
    client = TestClient(app)
    auth = app.state.mcp_auth_store
    private_client = auth.create_oauth_client("private-admin", "connectors:read connectors:write webhooks:private_network")
    private_token = auth.issue_client_credentials_token(private_client["client_id"], private_client["client_secret"])
    reader_client = auth.create_oauth_client("scoped-admin", "connectors:read connectors:write")
    reader_token = auth.issue_client_credentials_token(reader_client["client_id"], reader_client["client_secret"])
    private_headers = {"Authorization": f"Bearer {private_token['access_token']}"}
    reader_headers = {"Authorization": f"Bearer {reader_token['access_token']}"}
    assert client.post("/v1/webhooks", json={
        "id": "oauth-out", "url": "https://receiver.example/hook", "config": {"allow_private": True},
    }, headers=private_headers).status_code == 200
    denied = client.put("/v1/webhooks/oauth-out", json={
        "url": "http://127.0.0.1:8088/new-hook",
    }, headers=reader_headers)
    assert denied.status_code == 403
