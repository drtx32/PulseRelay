from __future__ import annotations

import asyncio
import ipaddress

import core.webhook_delivery as webhook_delivery
from core.persistence import DestinationRecord
from core.redaction import redact_sensitive_text, redact_url
from core.persistence import SQLitePhase9Store
from core.webhook_delivery import WebhookDelivery


def test_ssrf_blocks_non_global_addresses(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_delivery, "_resolve_addresses",
                        lambda host, port: [ipaddress.ip_address("127.0.0.1")])
    destination = DestinationRecord("local", "Local", True, "http://internal.example/hook", "POST", {})
    called = False

    def should_not_send(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("blocked destination was sent")

    monkeypatch.setattr(webhook_delivery, "_safe_http_request", should_not_send)
    result = asyncio.run(WebhookDelivery(destination).send({"content": {"text": "x"}}, "e", "d"))
    assert result.status == "failed"
    assert result.error == "destination URL rejected"
    assert not called


def test_redirect_response_is_not_followed(monkeypatch):
    calls = []

    class FakeResponse:
        status = 302

        def read(self, _limit):
            return b"https://private.example/hook/secret"

        def getheader(self, _name):
            return None

    monkeypatch.setattr(webhook_delivery, "_resolve_addresses",
                        lambda host, port: [ipaddress.ip_address("93.184.216.34")])
    monkeypatch.setattr(webhook_delivery.http.client.HTTPConnection, "request",
                        lambda self, method, path, **kwargs: calls.append((method, path)))
    monkeypatch.setattr(webhook_delivery.http.client.HTTPConnection, "getresponse", lambda self: FakeResponse())
    status, _, _ = webhook_delivery._safe_http_request(
        "http://public.example/start", "POST", {}, b"{}", 1.0
    )
    assert status == 302
    assert calls == [("POST", "/start")]


def test_secret_webhook_urls_and_delivery_diagnostics_are_redacted(tmp_path):
    secret_url = "https://user:password@relay.example/v1/hooks/in-a/0123456789abcdefghijklmnopqrstuv?token=secret-value"
    assert redact_url(secret_url) == (
        "https://relay.example/v1/hooks/in-a/<redacted>?token=%3Credacted%3E"
    )
    assert "password" not in redact_sensitive_text(f"request failed for {secret_url}")
    assert "secret-value" not in redact_sensitive_text(f"request failed for {secret_url}")
    store = SQLitePhase9Store(tmp_path / "redaction.db")
    store.create_delivery("delivery-1", "outbound", "idempotency-1")
    store.update_delivery("delivery-1", "failed", request_excerpt=f"URL {secret_url}",
                          response_excerpt="Authorization: Bearer response-secret", error=secret_url)
    saved = store.get_delivery("delivery-1")
    assert "secret-value" not in saved.request_excerpt
    assert "response-secret" not in saved.response_excerpt
    assert "password" not in saved.last_error
