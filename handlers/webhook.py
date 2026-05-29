"""
Generic Webhook delivery handler.

Delivers events by POSTing JSON to a configurable URL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.delivery_handler import (
    DeliveryCapabilities,
    DeliveryHandler,
    DeliveryManifest,
    DeliveryMessage,
    DeliveryResult,
)

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    """Configuration for WebhookDeliveryHandler."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    retry_count: int = 3


class WebhookDeliveryHandler(DeliveryHandler):
    """
    Delivery handler that POSTs JSON to a webhook URL.

    The handler serializes the DeliveryMessage to JSON and sends it via
    HTTP POST (or other configured method) to the target URL.
    """

    def __init__(self, config: WebhookConfig, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def manifest(self) -> DeliveryManifest:
        return DeliveryManifest(
            id="webhook",
            name="Webhook Delivery",
            version="0.1.0",
            description="Delivers events to a configurable webhook URL",
            capabilities=DeliveryCapabilities(
                supports_text=True,
                supports_markdown=True,
                supports_html=True,
                supports_files=True,
            ),
            tags=["webhook", "http", "post"],
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def deliver(
        self, message: DeliveryMessage, event: Any = None
    ) -> DeliveryResult:
        """
        Deliver a message by POSTing JSON to the webhook URL.

        The JSON payload includes the message fields and any event metadata.
        """

        payload = {
            "title": message.title,
            "text": message.text,
            "markdown": message.markdown,
            "html": message.html,
            "files": message.files,
            "metadata": message.metadata,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "PulseRelay-Webhook/0.1.0",
        }
        headers.update(self.config.headers)

        retry_count = self.config.retry_count
        last_error = ""

        for attempt in range(retry_count):
            try:
                client = await self._get_client()
                response = await client.request(
                    method=self.config.method,
                    url=self.config.url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

                return DeliveryResult(
                    handler_id=self.handler_id,
                    status="success",
                    message=f"Webhook delivered successfully",
                    external_id=str(response.status_code),
                    metadata={
                        "status_code": response.status_code,
                        "response_headers": dict(response.headers),
                    },
                )

            except httpx.TimeoutException as exc:
                last_error = f"Timeout after {self.config.timeout}s"
                logger.warning(
                    f"Webhook timeout (attempt {attempt + 1}/{retry_count}): {last_error}"
                )

            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}: {exc.response.text}"
                logger.warning(
                    f"Webhook HTTP error (attempt {attempt + 1}/{retry_count}): {last_error}"
                )
                # Don't retry on 4xx errors (client errors)
                if 400 <= exc.response.status_code < 500:
                    break

            except httpx.RequestError as exc:
                last_error = str(exc)
                logger.warning(
                    f"Webhook request error (attempt {attempt + 1}/{retry_count}): {last_error}"
                )

            except Exception as exc:
                last_error = str(exc)
                logger.error(f"Webhook unexpected error: {last_error}")
                break

        self.state = "error"
        self.last_error = last_error
        return DeliveryResult(
            handler_id=self.handler_id,
            status="failed",
            error=last_error,
        )
