"""
Compatibility layer for migrating existing source modules.

The old source modules were message-centric.
This module bridges them into the new SourceAdapter abstraction.
"""

from __future__ import annotations

from typing import Any

from core.event import EventEnvelope, ensure_event
from core.event_bus import EventBus
from core.source_adapter import SourceAdapter, SourceManifest


class LegacyMessageSourceAdapter(SourceAdapter):
    """Compatibility wrapper around existing message-producing sources."""

    manifest = SourceManifest(
        id="legacy-message-source",
        type="message",
        name="Legacy Message Source",
        supports_websocket=True,
        capabilities=["message-stream"],
        labels=["legacy", "compatibility"],
    )

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.connected = False

    async def connect(self):
        self.connected = True
        self.health.connected = True

    async def disconnect(self):
        self.connected = False
        self.health.connected = False

    async def receive(self, payload: Any):
        event = self.normalize(payload)
        return self.publish(event)

    def normalize(self, payload: Any) -> EventEnvelope:
        return ensure_event(payload, source_type=self.manifest.type)
