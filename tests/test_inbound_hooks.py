from __future__ import annotations

import hashlib
import hmac
import json
import time
import pytest

from fastapi.testclient import TestClient

from api import _hash_secret, _render, _valid_secret, create_app
from core.persistence import SQLitePhase9Store


@pytest.fixture(autouse=True)
def configure_admin_auth(monkeypatch):
    monkeypatch.setenv("PULSERELAY_ADMIN_TOKEN", "test-admin-token")


def make_client(tmp_path):
    store = SQLitePhase9Store(tmp_path / "hooks.db")
    return TestClient(create_app(store), headers={"Authorization": "Bearer test-admin-token"}), store


@pytest.mark.parametrize("challenge_name", ["challenge", "echostr", "validationToken", "hub.challenge"])
def test_get_hook_echoes_supported_challenge_as_plain_text(tmp_path, challenge_name):
    client, store = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "challenge-hook"}).json()

    response = client.get(created["url"] + f"?{challenge_name}=verify%2Bvalue")

    assert response.status_code == 200
    assert response.text == "verify+value"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert store.stats()["events_total"] == 0


def test_get_hook_rejects_missing_or_repeated_challenge(tmp_path):
    client, _ = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "challenge-hook"}).json()

    assert client.get(created["url"]).status_code == 400
    repeated = client.get(created["url"] + "?challenge=one&challenge=two")
    assert repeated.status_code == 400


def test_get_hook_rejects_wrong_secret_and_disabled_endpoint(tmp_path):
    client, _ = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "challenge-hook"}).json()
    endpoint_path = created["url"].rsplit("/", 1)[0]

    assert client.get(endpoint_path + "/wrong-secret?challenge=ok").status_code == 404
    assert client.put("/v1/hooks/challenge-hook", json={"enabled": False}).status_code == 200
    assert client.get(created["url"] + "?challenge=ok").status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_entity_http_methods_ingest_json_events(tmp_path, method):
    client, store = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "method-hook"}).json()
    payload = {"id": f"event-{method.lower()}", "event": {"type": "method.test"},
               "content": {"text": f"sent with {method}"}}

    response = client.request(method, created["url"], json=payload)

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert store.stats()["events_total"] == 1


def test_head_and_options_probe_hook_without_ingesting_events(tmp_path):
    client, store = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "method-hook"}).json()

    head = client.head(created["url"])
    options = client.options(created["url"])

    assert head.status_code == 200
    assert head.content == b""
    assert options.status_code == 204
    assert options.headers["allow"] == "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
    assert store.stats()["events_total"] == 0


def test_generated_hook_secret_is_returned_once_and_payload_is_persisted(tmp_path):
    client, store = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "source-a", "name": "Source A"})
    assert created.status_code == 200
    data = created.json()
    assert len(data["secret"]) >= 16
    assert data["url"].endswith(data["secret"])
    assert "secret_hash" not in client.get("/v1/hooks").json()["hooks"][0]

    payload = {"id": "evt-hook-1", "event": {"type": "source.created", "dedupe_key": "source:1"},
               "source": {"type": "external", "id": "source-a"}, "content": {"text": "hello"}}
    response = client.post(data["url"], json=payload)
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "event_id": "evt-hook-1", "duplicate": False}
    assert store.stats()["events_total"] == 1

    duplicate = client.post(data["url"], json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["event_id"] == "evt-hook-1"


def test_hook_rejects_wrong_secret_and_oversized_body(tmp_path):
    client, _ = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "limited", "max_body_bytes": 8}).json()
    assert client.post(f"/v1/hooks/limited/wrong-secret", json={}).status_code == 404
    response = client.post(created["url"], content=b'{"too":"large"}', headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_hook_hmac_requires_fresh_timestamp_and_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOK_SIGNING_SECRET", "signing-secret")
    client, _ = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "signed", "hmac_secret_ref": "HOOK_SIGNING_SECRET"}).json()
    body = json.dumps({"id": "signed-1", "event": {"type": "signed"}, "content": {"text": "ok"}}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"signing-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"content-type": "application/json", "X-PulseRelay-Timestamp": timestamp,
               "X-PulseRelay-Signature": f"sha256={signature}"}
    assert client.post(created["url"], content=body, headers=headers).status_code == 200
    headers["X-PulseRelay-Signature"] = "bad"
    assert client.post(created["url"], content=body, headers=headers).status_code == 401


def test_hook_secret_uses_salted_slow_hash_and_raw_payload_is_deduplicated(tmp_path):
    client, store = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "raw"}).json()
    endpoint = store.get_inbound_endpoint("raw")
    assert endpoint.secret_hash.startswith("pbkdf2_sha256$")
    assert endpoint.secret_hash != hashlib.sha256(created["secret"].encode()).hexdigest()
    assert _valid_secret(created["secret"], endpoint.secret_hash)
    assert not _valid_secret("wrong-secret-value", endpoint.secret_hash)

    payload = {"message": "same payload"}
    first = client.post(created["url"], json=payload).json()
    second = client.post(created["url"], json=payload).json()
    assert first["duplicate"] is False
    assert second == {"accepted": True, "event_id": first["event_id"], "duplicate": True}


def test_hmac_rejects_non_finite_timestamp_and_sandbox_rejects_code_like_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOK_SIGNING_SECRET", "signing-secret")
    client, _ = make_client(tmp_path)
    created = client.post("/v1/hooks", json={"id": "finite", "hmac_secret_ref": "HOOK_SIGNING_SECRET"}).json()
    body = b'{"id":"nan-1","event":{"type":"signed"},"content":{"text":"ok"}}'
    signature = hmac.new(b"signing-secret", b"nan." + body, hashlib.sha256).hexdigest()
    response = client.post(created["url"], content=body, headers={"content-type": "application/json",
        "X-PulseRelay-Timestamp": "nan", "X-PulseRelay-Signature": signature})
    assert response.status_code == 401

    with pytest.raises(ValueError): _render("{% for x in range(1000000) %}x{% endfor %}", event={}, duplicate=False)
    with pytest.raises(Exception): _render("{{ event.__class__ }}", event={}, duplicate=False)
