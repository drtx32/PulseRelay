"""
Source adapter abstractions.

A source adapter is responsible for:

- connecting to an external source
- receiving raw events/messages
- normalizing them into EventEnvelope
- publishing events into the EventBus
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.event import EventEnvelope, EventSource
from core.event_bus import EventBus


@dataclass
class SourceHealth:
    connected: bool = False
    last_event_at: str = ""
    last_error: str = ""
    reconnect_count: int = 0


@dataclass
class SourceManifest:
    id: str
    type: str
    name: str

    supports_webhook: bool = False
    supports_websocket: bool = False
    supports_replay: bool = False
    supports_ack: bool = False

    capabilities: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    """Base class for all event sources."""

    manifest: SourceManifest

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.health = SourceHealth()

    @abstractmethod
    async def connect(self):
        """Connect to the external source."""

    @abstractmethod
    async def disconnect(self):
        """Disconnect from the external source."""

    @abstractmethod
    async def receive(self, payload: Any):
        """Handle a raw payload from the source."""

    @abstractmethod
    def normalize(self, payload: Any) -> EventEnvelope:
        """Convert raw payloads into EventEnvelope."""

    def publish(self, event: EventEnvelope):
        """Publish an event into the EventBus."""
        self.health.last_event_at = datetime.now(timezone.utc).isoformat()
        return self.event_bus.put(self.manifest.type, event)

    def create_source(self) -> EventSource:
        return EventSource(
            type=self.manifest.type,
            id=self.manifest.id,
            name=self.manifest.name,
        )

    def get_health(self) -> dict:
        return {
            "manifest": self.manifest.__dict__,
            "health": self.health.__dict__,
        }
