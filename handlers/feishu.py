"""
Feishu (Lark) delivery handler using webhook API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from core.delivery_handler import (
    DeliveryCapabilities,
    DeliveryHandler,
    DeliveryManifest,
    DeliveryMessage,
    DeliveryResult,
)
from core.event import EventEnvelope


class FeishuDeliveryHandler(DeliveryHandler):
    """Delivery handler for Feishu webhook notifications."""

    manifest = DeliveryManifest(
        id="feishu",
        name="Feishu",
        version="0.1.0",
        description="Sends card messages to Feishu via webhook",
        capabilities=DeliveryCapabilities(supports_text=True),
    )

    def __init__(self, webhook_url: str, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.webhook_url = webhook_url

    async def deliver(
        self, message: DeliveryMessage, event: EventEnvelope | None = None
    ) -> DeliveryResult:
        """Send a card message to Feishu webhook."""

        payload = self._build_card_payload(message)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.webhook_url,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                )
                response.raise_for_status()
                result = response.json()

                if result.get("code") != 0:
                    return DeliveryResult(
                        handler_id=self.handler_id,
                        status="failed",
                        error=result.get("msg", "unknown error"),
                    )

                return DeliveryResult(
                    handler_id=self.handler_id,
                    status="success",
                    message="Feishu card sent",
                    external_id=result.get("data", {}).get("message_id", ""),
                )

        except httpx.HTTPError as exc:
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=f"HTTP error: {exc}",
            )
        except Exception as exc:
            return DeliveryResult(
                handler_id=self.handler_id,
                status="failed",
                error=str(exc),
            )

    def _build_card_payload(self, message: DeliveryMessage) -> dict[str, Any]:
        """Build Feishu card payload from delivery message."""

        title = message.title or "PulseRelay Event"
        content = message.text or ""

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content,
                        },
                    },
                ],
            },
        }
