"""
Phase 8 tests for GitHubWebhookSource.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from core.event_bus import EventBus
from sources.github_webhook import GitHubWebhookSource


def _build_push_payload() -> dict:
    return {
        "after": "abc123",
        "ref": "refs/heads/main",
        "repository": {
            "full_name": "octo-org/octo-repo",
            "html_url": "https://github.com/octo-org/octo-repo",
        },
        "sender": {"login": "octocat"},
        "head_commit": {"id": "abc123", "message": "feat: update pipeline"},
        "commits": [{"message": "feat: update pipeline"}],
    }


def test_phase8_github_signature_verification():
    payload = b'{"hello":"world"}'
    secret = "test_secret"
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    source = GitHubWebhookSource(event_bus=EventBus(), webhook_secret=secret)

    assert source.verify_signature(payload, signature) is True
    assert source.verify_signature(payload, "sha256=invalid") is False
    assert source.verify_signature(payload, "invalid-prefix") is False


@pytest.mark.asyncio
async def test_phase8_github_webhook_emits_event():
    bus = EventBus()
    source = GitHubWebhookSource(event_bus=bus, webhook_secret="")
    payload = _build_push_payload()

    event = await source.on_webhook(
        payload=payload,
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-1",
        },
    )

    assert event is not None
    assert event.event.type == "github.push"
    assert event.context.extra["github_delivery_id"] == "delivery-1"
    assert event.context.extra["github_event"] == "push"

    record = bus.get(timeout=0.1)
    assert record.key == "github_webhook"
    assert record.event.event.type == "github.push"
