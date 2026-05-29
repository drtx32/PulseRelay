"""
Sample Webhook Source Plugin.

This plugin demonstrates how to create a SourceAdapter plugin that
receives events via HTTP webhooks.
"""

import asyncio
import logging
from typing import Any

from core.event import EventEnvelope, EventSource, EventSender, EventMeta, EventContent, EventContext, EventPermissions
from core.event_bus import EventBus
from core.plugin.base import SourcePlugin
from core.plugin.manifest import PluginMetadata, PluginCapabilities, PluginEntryPoint

logger = logging.getLogger(__name__)


class WebhookSourcePlugin(SourcePlugin):
    """
    Sample source adapter plugin that receives events via webhook.

    This is a demonstration plugin that can be extended to receive
    webhooks from various services (GitHub, Slack, etc.).
    """

    metadata = PluginMetadata(
        id="webhook-source",
        name="Webhook Source",
        version="1.0.0",
        description="Sample source adapter that receives events via HTTP webhooks",
        author="PulseRelay",
        tags=["webhook", "http", "source"],
        capabilities=PluginCapabilities(
            supports_webhook=True,
            supports_websocket=False,
            supports_polling=False,
        ),
        entry_points=[PluginEntryPoint.SOURCE],
    )

    def __init__(self, config: dict[str, Any] | None = None, event_bus: EventBus | None = None):
        super().__init__(config)
        self._event_bus = event_bus
        self._running = False
        self._webhook_queue: asyncio.Queue = asyncio.Queue()
        self._port = self.config.get("port", 9000)
        self._host = self.config.get("host", "0.0.0.0")

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Set the event bus for publishing events."""
        self._event_bus = event_bus

    async def initialize(self) -> None:
        """Initialize the webhook source."""
        logger.info(f"Initializing WebhookSource plugin on {self._host}:{self._port}")
        self._running = True

    async def terminate(self) -> None:
        """Terminate the webhook source."""
        logger.info("Terminating WebhookSource plugin")
        self._running = False

    async def run(self) -> None:
        """
        Main event loop for the webhook source.

        In a real implementation, this would start an HTTP server.
        This sample demonstrates the plugin structure.
        """
        logger.info(f"WebhookSource plugin running (simulating webhook receiver on {self._host}:{self._port})")

        while self._running:
            try:
                # Simulate processing - in real impl this would receive actual webhook payloads
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebhookSource error: {e}")

    async def receive_webhook(self, payload: dict[str, Any]) -> EventEnvelope:
        """
        Process an incoming webhook payload and emit an event.

        This method can be called by an HTTP endpoint handler.

        Args:
            payload: The webhook payload dictionary

        Returns:
            The created EventEnvelope
        """
        if self._event_bus is None:
            raise RuntimeError("Event bus not set")

        # Create event from webhook payload
        event = EventEnvelope(
            source=EventSource(type="webhook", id="webhook-source", name="Webhook Source"),
            sender=EventSender(
                id=payload.get("sender_id", "unknown"),
                name=payload.get("sender_name", "Unknown Sender"),
                trust_level="user",
            ),
            event=EventMeta(type=payload.get("event_type", "webhook.message")),
            content=EventContent(
                title=payload.get("title", "Webhook Event"),
                text=payload.get("text", str(payload)),
            ),
            context=EventContext(conversation_id=payload.get("conversation_id", "webhook")),
            permissions=EventPermissions(max_risk_level="read"),
        )

        await self.emit(event)
        return event

    async def emit(self, event: EventEnvelope) -> EventEnvelope:
        """Publish a normalized event into the event bus."""
        if self._event_bus is None:
            raise RuntimeError("Event bus not set")

        self._event_bus.put(self.plugin_id, event)
        logger.debug(f"Emitted event: {event.event.type}")
        return event


# Export the plugin class for auto-registration
Plugin = WebhookSourcePlugin
