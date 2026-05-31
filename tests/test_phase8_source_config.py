"""
Phase 8 config wiring tests for source adapters.
"""

from __future__ import annotations

from base import Signals
import gateway
from sources.github_webhook import GitHubWebhookSource


class _FakeTelegramSource:
    def __init__(self, event_bus, bot_token: str, allowed_chat_ids=None, enabled: bool = True):
        self.event_bus = event_bus
        self.bot_token = bot_token
        self.allowed_chat_ids = allowed_chat_ids or []
        self.enabled = enabled


class _FakeSlackSource:
    def __init__(
        self,
        event_bus,
        app_token: str,
        bot_token: str,
        allowed_channels=None,
        enabled: bool = True,
    ):
        self.event_bus = event_bus
        self.app_token = app_token
        self.bot_token = bot_token
        self.allowed_channels = allowed_channels or []
        self.enabled = enabled


def test_phase8_build_sources_from_runtime_config(monkeypatch):
    monkeypatch.setattr(gateway, "TelegramSource", _FakeTelegramSource)
    monkeypatch.setattr(gateway, "SlackSource", _FakeSlackSource)

    runtime_config = {
        "sources": {
            "weflow": {
                "enabled": False,
                "connection": {
                    "host": "127.0.0.1",
                    "port": 5031,
                    "access_token": "token",
                },
            },
            "telegram": {
                "enabled": True,
                "connection": {"bot_token": "tg-token"},
                "monitor": {"allowed_chat_ids": ["1001", "1002"]},
            },
            "slack": {
                "enabled": True,
                "connection": {"app_token": "xapp-1", "bot_token": "xoxb-1"},
                "monitor": {"allowed_channels": ["C123"]},
            },
            "github_webhook": {
                "enabled": True,
                "webhook": {"secret": "gh-secret"},
            },
        }
    }

    sources = gateway._build_sources(Signals(), runtime_config)

    assert "weflow" in sources
    assert sources["weflow"].enabled is False

    assert "telegram" in sources
    assert sources["telegram"].bot_token == "tg-token"
    assert sources["telegram"].allowed_chat_ids == ["1001", "1002"]

    assert "slack" in sources
    assert sources["slack"].app_token == "xapp-1"
    assert sources["slack"].allowed_channels == ["C123"]

    assert "github_webhook" in sources
    assert isinstance(sources["github_webhook"], GitHubWebhookSource)


def test_phase8_build_sources_skips_missing_credentials(monkeypatch):
    monkeypatch.setattr(gateway, "TelegramSource", _FakeTelegramSource)
    monkeypatch.setattr(gateway, "SlackSource", _FakeSlackSource)

    runtime_config = {
        "sources": {
            "telegram": {
                "enabled": True,
                "connection": {"bot_token": ""},
            },
            "slack": {
                "enabled": True,
                "connection": {"app_token": "xapp-1", "bot_token": ""},
            },
            "github_webhook": {
                "enabled": False,
            },
        }
    }

    sources = gateway._build_sources(Signals(), runtime_config)

    assert "telegram" not in sources
    assert "slack" not in sources
    assert "github_webhook" not in sources


def test_phase8_get_github_webhook_path():
    assert gateway._get_github_webhook_path({}) == "/webhooks/github"

    assert gateway._get_github_webhook_path(
        {"sources": {"github_webhook": {"webhook": {"path": "/hook/github"}}}}
    ) == "/hook/github"

    assert gateway._get_github_webhook_path(
        {"sources": {"github_webhook": {"webhook": {"path": "invalid-path"}}}}
    ) == "/webhooks/github"
