"""
Delivery handler abstractions.

A delivery handler is responsible for sending event results to humans, tools,
or external systems.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from core.event import EventEnvelope

DeliveryState = Literal["created", "ready", "degraded", "disabled", "error"]
DeliveryStatus = Literal["success", "failed", "skipped"]


@dataclass
class DeliveryCapabilities:
    """Capabilities exposed by a delivery handler."""

    supports_markdown: bool = False
    supports_html: bool = False
    supports_files: bool = False
    supports_threads: bool = False
    supports_update: bool = False
    supports_webhook: bool = False


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
class DeliveryHealth:
    """Runtime health metadata for a delivery handler."""

    state: DeliveryState = "created"
    last_delivery_at: str = ""
    last_error: str = ""
    delivery_count: int = 0
    failure_count: int = 0


@dataclass
class DeliveryRequest:
    """A normalized delivery request."""

    event: EventEnvelope
    title: str = ""
    body: str = ""
    target: str = ""
    thread_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    """Result returned by a delivery handler."""

    id: str = field(default_factory=lambda: f"del_{uuid4().hex}")
    handler_id: str = ""
    status: DeliveryStatus = "success"
    message: str = ""
    external_id: str = ""
    external_url: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class DeliveryHandler(ABC):
    """Base class for outbound delivery integrations."""

    manifest: DeliveryManifest

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.health = DeliveryHealth(state="ready" if enabled else "disabled")

    @property
    def handler_id(self) -> str:
        return self.manifest.id

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        """Deliver a normalized request and update health metadata."""

        if not self.enabled:
            return DeliveryResult(
                handler_id=self.handler_id,
                status="skipped",
                message="delivery handler is disabled",
            )

        try:
            result = await self.send(request)
            result.handler_id = result.handler_id or self.handler_id
            self.health.last_delivery_at = datetime.now(timezone.utc).isoformat()
            self.health.delivery_count += 1
            self.health.state = "ready"
            return result
        except Exception as exc:
            self.health.failure_count += 1
            self.health.last_error = str(exc)
            self.health.state = "error"
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=str(exc),
            )

    @abstractmethod
    async def send(self, request: DeliveryRequest) -> DeliveryResult:
        """Send a delivery request to the external destination."""
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

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            handler.handler_id: {
                "state": handler.health.state,
                "last_delivery_at": handler.health.last_delivery_at,
                "last_error": handler.health.last_error,
                "delivery_count": handler.health.delivery_count,
                "failure_count": handler.health.failure_count,
            }
            for handler in self._handlers.values()
        }
