"""
Delivery handler abstractions.

A delivery handler is responsible for sending processed events or trigger
results to humans, systems, or downstream agents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from core.event import EventEnvelope

DeliveryState = Literal["created", "ready", "disabled", "error"]
DeliveryStatus = Literal["success", "skipped", "failed"]


@dataclass
class DeliveryCapabilities:
    """Capabilities exposed by a delivery handler."""

    supports_text: bool = True
    supports_markdown: bool = False
    supports_html: bool = False
    supports_files: bool = False
    supports_streaming: bool = False
    supports_actions: bool = False


@dataclass
class DeliveryManifest:
    """Static metadata describing a delivery handler."""

    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    capabilities: DeliveryCapabilities = field(default_factory=DeliveryCapabilities)
    tags: list[str] = field(default_factory=list)


@dataclass
class DeliveryMessage:
    """Normalized outbound message produced from an event."""

    title: str = ""
    text: str = ""
    markdown: str = ""
    html: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: EventEnvelope) -> "DeliveryMessage":
        return cls(
            title=event.content.title or event.event.type,
            text=event.content.text,
            html=event.content.html,
            files=event.content.files,
            metadata={
                "event_id": event.id,
                "source": event.source.__dict__,
                "sender": event.sender.__dict__,
                "event": event.event.__dict__,
                "context": event.context.__dict__,
                "routing": event.routing.__dict__,
            },
        )


@dataclass
class DeliveryResult:
    """Result returned by a delivery handler."""

    id: str = field(default_factory=lambda: f"del_{uuid4().hex}")
    handler_id: str = ""
    status: DeliveryStatus = "success"
    message: str = ""
    external_id: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class DeliveryHandler(ABC):
    """
    Base class for all outbound delivery handlers.

    Delivery handlers should not decide routing. They only know how to deliver
    a normalized DeliveryMessage to one destination type.
    """

    manifest: DeliveryManifest

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.state: DeliveryState = "ready" if enabled else "disabled"
        self.last_error: str = ""

    @property
    def handler_id(self) -> str:
        return self.manifest.id

    def build_message(self, event: EventEnvelope) -> DeliveryMessage:
        """Convert an event into a default outbound message."""

        return DeliveryMessage.from_event(event)

    async def deliver_event(self, event: EventEnvelope) -> DeliveryResult:
        """Build and deliver a message from an event."""

        if not self.enabled:
            return DeliveryResult(
                handler_id=self.handler_id,
                status="skipped",
                message="delivery handler disabled",
            )

        try:
            message = self.build_message(event)
            return await self.deliver(message, event=event)
        except Exception as exc:
            self.state = "error"
            self.last_error = str(exc)
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=str(exc),
            )

    @abstractmethod
    async def deliver(self, message: DeliveryMessage, event: EventEnvelope | None = None) -> DeliveryResult:
        """Deliver a normalized outbound message."""
        raise NotImplementedError


class DeliveryRegistry:
    """Runtime registry for delivery handlers."""

    def __init__(self):
        self._handlers: dict[str, DeliveryHandler] = {}

    def register(self, handler: DeliveryHandler):
        self._handlers[handler.handler_id] = handler

    def get(self, handler_id: str) -> DeliveryHandler | None:
        return self._handlers.get(handler_id)

    def list(self) -> list[DeliveryHandler]:
        return list(self._handlers.values())

    def state_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            handler.handler_id: {
                "state": handler.state,
                "enabled": handler.enabled,
                "last_error": handler.last_error,
            }
            for handler in self._handlers.values()
        }
