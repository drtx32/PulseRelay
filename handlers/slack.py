"""
Slack delivery handler via Incoming Webhooks.
"""

from __future__ import annotations

import logging
import requests
from typing import Any

from core.delivery_handler import (
    DeliveryHandler,
    DeliveryManifest,
    DeliveryCapabilities,
    DeliveryMessage,
    DeliveryResult,
)
from core.event import EventEnvelope

logger = logging.getLogger(__name__)


class SlackHandler(DeliveryHandler):
    """Delivery handler for Slack via Incoming Webhooks."""

    manifest = DeliveryManifest(
        id="slack",
        name="Slack",
        version="0.1.0",
        description="Send notifications to Slack via Incoming Webhooks",
        capabilities=DeliveryCapabilities(
            supports_text=True,
            supports_markdown=True,
            supports_html=False,
            supports_files=False,
            supports_streaming=False,
            supports_actions=True,
        ),
        tags=["notification", "slack"],
    )

    def __init__(
        self,
        webhook_url: str | None = None,
        default_channel: str | None = None,
        enabled: bool = True,
    ):
        super().__init__(enabled=enabled)
        self.webhook_url = webhook_url
        self.default_channel = default_channel

    async def deliver(
        self, message: DeliveryMessage, event: EventEnvelope | None = None
    ) -> DeliveryResult:
        """Deliver a message to Slack via Incoming Webhook."""
        if not self.webhook_url:
            self.state = "error"
            self.last_error = "SLACK_WEBHOOK_URL not configured"
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=self.last_error,
            )

        try:
            payload = self._build_slack_payload(message)
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"Slack notification sent: {message.title or message.text[:50]}")
            return DeliveryResult(
                handler_id=self.handler_id,
                status="success",
                message="Slack notification delivered",
                metadata={"status_code": resp.status_code},
            )
        except requests.RequestException as exc:
            self.state = "error"
            self.last_error = str(exc)
            logger.error(f"Slack notification failed: {exc}")
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=str(exc),
            )

    def _build_slack_payload(self, message: DeliveryMessage) -> dict[str, Any]:
        """Build Slack Block Kit payload from DeliveryMessage."""
        blocks = []

        # Title as header if present
        if message.title:
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": message.title[:150],  # Slack header limit
                        "emoji": True,
                    },
                }
            )

        # Main text content (prefer markdown)
        body = message.markdown or message.text
        if body:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": body,
                    },
                }
            )

        # Add files as attachments if present
        attachments = []
        for f in message.files:
            attachments.append(
                {
                    "color": "#36a64f",
                    "fields": [
                        {
                            "title": f.get("name", "File"),
                            "value": f.get("url", ""),
                            "short": False,
                        }
                    ],
                }
            )

        payload: dict[str, Any] = {}
        if blocks:
            payload["blocks"] = blocks
        if attachments:
            payload["attachments"] = attachments

        # Fallback text
        payload["text"] = message.title or message.text or ""

        # Override channel if set
        if self.default_channel:
            payload["channel"] = self.default_channel

        return payload
