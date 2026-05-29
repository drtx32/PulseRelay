"""
Sample Log Delivery Plugin.

This plugin demonstrates how to create a DeliveryHandler plugin that
logs events/messages to a file or stdout.
"""

import logging
from typing import Any
from datetime import datetime

from core.event import EventEnvelope
from core.delivery_handler import DeliveryMessage, DeliveryResult
from core.plugin.base import DeliveryPlugin
from core.plugin.manifest import PluginMetadata, PluginCapabilities, PluginEntryPoint

logger = logging.getLogger(__name__)


class LogDeliveryPlugin(DeliveryPlugin):
    """
    Sample delivery handler plugin that logs messages.

    This is a demonstration plugin that shows how to implement
    a DeliveryHandler plugin for debugging and logging purposes.
    """

    metadata = PluginMetadata(
        id="log-delivery",
        name="Log Delivery",
        version="1.0.0",
        description="Sample delivery handler that logs events/messages for debugging",
        author="PulseRelay",
        tags=["log", "debug", "delivery"],
        capabilities=PluginCapabilities(
            supports_text=True,
            supports_markdown=True,
        ),
        entry_points=[PluginEntryPoint.DELIVERY],
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._log_level = self.config.get("log_level", "INFO").upper()
        self._log_to_file = self.config.get("log_to_file", False)
        self._log_file_path = self.config.get("log_file_path", "/tmp/pulserelay_delivery.log")
        self._prefix = self.config.get("prefix", "[LogDelivery]")

    async def initialize(self) -> None:
        """Initialize the log delivery handler."""
        logger.info(f"Initializing LogDelivery plugin (level={self._log_level}, file={self._log_to_file})")
        if self._log_to_file:
            try:
                with open(self._log_file_path, "a") as f:
                    f.write(f"\n{'='*60}\nLogDelivery started at {datetime.now().isoformat()}\n{'='*60}\n")
            except Exception as e:
                logger.error(f"Failed to open log file: {e}")

    async def terminate(self) -> None:
        """Terminate the log delivery handler."""
        logger.info("Terminating LogDelivery plugin")
        if self._log_to_file:
            try:
                with open(self._log_file_path, "a") as f:
                    f.write(f"\n{'='*60}\nLogDelivery stopped at {datetime.now().isoformat()}\n{'='*60}\n")
            except Exception as e:
                logger.error(f"Failed to write to log file: {e}")

    async def deliver(self, message: DeliveryMessage, event: EventEnvelope | None = None) -> DeliveryResult:
        """
        Deliver a message by logging it.

        Args:
            message: The delivery message to log
            event: Optional original event

        Returns:
            DeliveryResult indicating success/failure
        """
        try:
            log_entry = self._format_log_entry(message, event)

            # Log to standard logger
            log_fn = getattr(logger, self._log_level.lower(), logger.info)
            log_fn(f"{self._prefix} {log_entry}")

            # Optionally log to file
            if self._log_to_file:
                try:
                    with open(self._log_file_path, "a") as f:
                        f.write(f"{datetime.now().isoformat()} {self._prefix} {log_entry}\n")
                except Exception as e:
                    logger.error(f"Failed to write to log file: {e}")

            return DeliveryResult(
                handler_id=self.plugin_id,
                status="success",
                message=f"Logged: {message.title or message.text[:50]}",
            )

        except Exception as exc:
            logger.error(f"LogDelivery error: {exc}")
            return DeliveryResult(
                handler_id=self.plugin_id,
                status="failed",
                error=str(exc),
            )

    def _format_log_entry(self, message: DeliveryMessage, event: EventEnvelope | None) -> str:
        """Format a log entry from the message and optional event."""
        parts = []

        if message.title:
            parts.append(f"Title: {message.title}")

        if message.text:
            parts.append(f"Text: {message.text[:200]}")

        if message.markdown:
            parts.append(f"Markdown: {message.markdown[:200]}")

        if event:
            parts.append(f"Event: {event.event.type} from {event.source.name}")

        return " | ".join(parts) if parts else "Empty message"


# Export the plugin class for auto-registration
Plugin = LogDeliveryPlugin
