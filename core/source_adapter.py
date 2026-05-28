"""
Source adapter abstractions.

A source adapter is responsible for:

- connecting to external systems
- receiving realtime events
- normalizing raw payloads into EventEnvelope
- publishing events into the EventBus
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.event import EventEnvelope, ensure_event
from core.event_bus import EventBus


SourceState = Literal[
    "created",
    "starting",
    "running",
    "reconnecting",
    "stopped",
    "error",
]


@dataclass
class SourceCapabilities:
    """Capabilities exposed by a source adapter."""

    supports_websocket: bool = False
    supports_webhook: bool = False
    supports_streaming: bool = False
    supports_ack: bool = False
    supports_replay: bool = False


@dataclass
class SourceHealth:
    """Runtime health metadata for a source adapter."""

    state: SourceState = "created"
    last_event_at: str = ""
    last_error: str = ""
    reconnect_count: int = 0


@dataclass
class SourceManifest:
    """Static metadata describing a source adapter."""

    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    capabilities: SourceCapabilities = field(default_factory=SourceCapabilities)
    tags: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    """
    Base class for all event sources.

    Source adapters are expected to:

    1. receive raw platform events
    2. normalize them into EventEnvelope
    3. publish them to the EventBus
    """

    manifest: SourceManifest

    def __init__(self, event_bus: EventBus, enabled: bool = True):
        self.event_bus = event_bus
        self.enabled = enabled
        self.health = SourceHealth()

    @property
    def source_id(self) -> str:
        return self.manifest.id

    async def emit(self, event: EventEnvelope | dict[str, Any]):
        """Publish a normalized event into the event bus."""

        normalized = ensure_event(event, source_type=self.source_id)

        self.health.last_event_at = datetime.now(timezone.utc).isoformat()

        self.event_bus.put(self.source_id, normalized)
        return normalized

    async def start(self):
        """Start the source adapter lifecycle."""

        self.health.state = "starting"

        try:
            self.health.state = "running"
            await self.run()
        except asyncio.CancelledError:
            self.health.state = "stopped"
            raise
        except Exception as exc:
            self.health.state = "error"
            self.health.last_error = str(exc)
            raise

    async def stop(self):
        self.health.state = "stopped"

    @abstractmethod
    async def run(self):
        """Main event loop for the source adapter."""
        raise NotImplementedError


class SourceRegistry:
    """Runtime registry for source adapters and legacy modules."""

    def __init__(self):
        self._sources: dict[str, Any] = {}

    def _resolve_source_id(self, source: Any, fallback: str | None = None) -> str:
        if hasattr(source, "source_id"):
            return str(source.source_id)

        manifest = getattr(source, "manifest", None)
        if manifest is not None and hasattr(manifest, "id"):
            return str(manifest.id)

        if fallback:
            return fallback

        return str(getattr(source, "name", source.__class__.__name__))

    def register(self, source: Any, source_id: str | None = None):
        resolved_id = self._resolve_source_id(source, fallback=source_id)
        self._sources[resolved_id] = source

    def get(self, source_id: str) -> Any | None:
        return self._sources.get(source_id)

    def list(self) -> list[Any]:
        return list(self._sources.values())

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}

        for source_id, source in self._sources.items():
            health = getattr(source, "health", None)
            snapshot[source_id] = {
                "state": getattr(health, "state", "legacy") if health else "legacy",
                "last_event_at": getattr(health, "last_event_at", "") if health else "",
                "last_error": getattr(health, "last_error", "") if health else "",
                "reconnect_count": getattr(health, "reconnect_count", 0) if health else 0,
            }

        return snapshot
