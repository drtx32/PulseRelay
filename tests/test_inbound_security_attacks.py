from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
from api import _hash_secret, _read_limited_body, _render, _valid_secret, create_app
from core.persistence import SQLitePhase9Store


def _client(tmp_path, **hook):
    store = SQLitePhase9Store(tmp_path / "attack.db")
    client = TestClient(create_app(store))
    created = client.post("/v1/hooks", json={"id": "attack", **hook})
    assert created.status_code == 200, created.text
    return client, store, created.json()


def _signed_request(client, url, body, timestamp, secret="hmac-secret"):
    timestamp = str(timestamp)
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return client.post(url, content=body, headers={
        "content-type": "application/json",
        "X-PulseRelay-Timestamp": timestamp,
        "X-PulseRelay-Signature": "sha256=" + signature,
    })


def test_pbkdf2_verifier_rejects_tampering_and_unsafe_parameters():
    verifier = _hash_secret("correct horse battery staple")
    assert _valid_secret("correct horse battery staple", verifier)
    assert not _valid_secret("wrong", verifier)
    parts = verifier.split("$")
    for tampered in (
        "pbkdf2_sha256$1$" + parts[2] + "$" + parts[3],
        "pbkdf2_sha256$100001$00$" + parts[3],
        "pbkdf2_sha256$100001$" + parts[2] + "$00",
        "pbkdf2_sha512$310000$" + parts[2] + "$" + parts[3],
        "pbkdf2_sha256$999999999$" + parts[2] + "$" + parts[3],
    ):
        assert not _valid_secret("correct horse battery staple", tampered)


def test_legacy_sha256_verifier_is_upgraded_after_successful_request(tmp_path):
    secret = "legacy-secret-value-123"
    store = SQLitePhase9Store(tmp_path / "legacy.db")
    store.create_inbound_endpoint("legacy", "Legacy", hashlib.sha256(secret.encode()).hexdigest())
    client = TestClient(create_app(store))
    response = client.post("/v1/hooks/legacy/" + secret, json={"id": "legacy-event"})
    assert response.status_code == 200
    assert store.get_inbound_endpoint("legacy").secret_hash.startswith("pbkdf2_sha256$")


@pytest.mark.parametrize("timestamp", ["nan", "inf", "-inf", "not-a-number", ""])
def test_hmac_rejects_non_finite_or_malformed_timestamps(tmp_path, monkeypatch, timestamp):
    monkeypatch.setenv("HMAC_ATTACK_SECRET", "hmac-secret")
    client, _, created = _client(tmp_path, hmac_secret_ref="HMAC_ATTACK_SECRET")
    body = b'{"id":"timestamp-attack"}'
    response = client.post(created["url"], content=body, headers={
        "content-type": "application/json", "X-PulseRelay-Timestamp": timestamp,
        "X-PulseRelay-Signature": "00",
    })
    assert response.status_code == 401


def test_hmac_window_has_explicit_past_and_future_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv("HMAC_ATTACK_SECRET", "hmac-secret")
    monkeypatch.setattr(api.time, "time", lambda: 1_000.0)
    client, _, created = _client(tmp_path, hmac_secret_ref="HMAC_ATTACK_SECRET", hmac_max_age_seconds=10)
    body = b'{"id":"timestamp-boundary"}'

    assert _signed_request(client, created["url"], body, 990).status_code == 200
    assert _signed_request(client, created["url"], body, 1010).status_code == 200
    assert _signed_request(client, created["url"], body, "989.999").status_code == 401
    assert _signed_request(client, created["url"], body, "1010.001").status_code == 401


def test_hmac_requires_signature_and_configured_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_HMAC_SECRET", raising=False)
    client, _, created = _client(tmp_path, hmac_secret_ref="MISSING_HMAC_SECRET")
    body = b'{"id":"missing-secret"}'
    response = client.post(created["url"], content=body, headers={
        "content-type": "application/json", "X-PulseRelay-Timestamp": "1000",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chunked_reader_rejects_overflow_without_content_length():
    class ChunkedRequest:
        async def stream(self):
            yield b"1234"
            yield b"5678"
            yield b"9"

    with pytest.raises(HTTPException) as error:
        await _read_limited_body(ChunkedRequest(), 8)
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_chunked_reader_accepts_exact_limit_and_preserves_chunks():
    class ChunkedRequest:
        async def stream(self):
            yield b"1234"
            yield b"5678"
            yield b""

    assert await _read_limited_body(ChunkedRequest(), 8) == b"12345678"


@pytest.mark.parametrize("template", [
    "{% for x in range(1000000) %}x{% endfor %}",
    "{% macro exploit() %}x{% endmacro %}",
    "{{ cycler(1, 2) }}",
    "{% import 'os' as os %}",
    "{% from 'os' import system %}",
    "{% include 'secret.txt' %}",
    "{% extends 'base.html' %}",
    "{{ event.__class__.__mro__ }}",
    "{{ event['__class__'] }}",
    "{{ event|attr('__class__') }}",
])
def test_template_sandbox_rejects_escape_and_resource_amplification(template):
    with pytest.raises(Exception):
        _render(template, event={"id": "safe"}, duplicate=False)


def test_template_source_and_output_limits_are_enforced():
    with pytest.raises(ValueError):
        _render("x" * (32 * 1024 + 1), event={}, duplicate=False)
    with pytest.raises(ValueError):
        _render("{{ event.text }}", event={"text": "x" * (256 * 1024 + 1)}, duplicate=False)
    assert _render("{{ event.id }}", event={"id": "safe"}, duplicate=False) == "safe"
