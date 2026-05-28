"""
WeFlow source adapter.

Consumes WeFlow HTTP SSE streams and publishes normalized EventEnvelope
objects into the EventBus.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import requests

from core.event import EventEnvelope, EventContent, EventContext, EventMeta, EventSender, EventSource
from core.source_adapter import (
    SourceAdapter,
    SourceCapabilities,
    SourceManifest,
)

logger = logging.getLogger(__name__)


class WeFlowSource(SourceAdapter):
    """WeFlow SSE source adapter."""

    manifest = SourceManifest(
        id="weflow",
        name="WeFlow Source",
        description="Consume WeFlow SSE streams and normalize chat events.",
        capabilities=SourceCapabilities(
            supports_streaming=True,
            supports_websocket=False,
        ),
        tags=["wechat", "sse", "stream"],
    )

    def __init__(
        self,
        event_bus,
        host: str,
        port: int,
        access_token: str,
        enabled: bool = True,
    ):
        super().__init__(event_bus=event_bus, enabled=enabled)

        self.host = host
        self.port = port
        self.access_token = access_token

        self.base_url = f"http://{self.host}:{self.port}/api/v1/push/messages"

    def normalize_event(self, raw: dict) -> EventEnvelope | None:
        """Normalize WeFlow payload into EventEnvelope."""

        rawid = raw.get("rawid", "")
        timestamp = raw.get("timestamp", 0)

        if timestamp and time.time() - timestamp > 60 * 5:
            logger.debug(
                f"Ignored old message: {raw.get('content', '')[:30]}..."
            )
            return None

        dt = datetime.fromtimestamp(timestamp) if timestamp else datetime.now()

        dedupe_key = f"weflow:{timestamp}:{rawid}" if rawid else f"weflow:{timestamp}"

        return EventEnvelope(
            source=EventSource(
                type="weflow",
                id=raw.get("sessionId", ""),
                name=raw.get("groupName", raw.get("sourceName", "")),
            ),
            sender=EventSender(
                id=raw.get("sourceName", ""),
                name=raw.get("sourceName", ""),
                trust_level="user",
            ),
            event=EventMeta(
                type="message.created",
                timestamp=dt.isoformat(),
                dedupe_key=dedupe_key,
            ),
            content=EventContent(
                title=raw.get("groupName", raw.get("sourceName", "")),
                text=raw.get("content", ""),
                raw=raw,
            ),
            context=EventContext(
                conversation_id=raw.get("sessionId", ""),
                channel_id=raw.get("sessionId", ""),
                extra={
                    "group_name": raw.get("groupName", ""),
                    "rawid": rawid,
                },
            ),
        )

    async def run(self):
        logger.info(f"WeFlowSource starting: {self.base_url}")

        while self.health.state != "stopped":
            try:
                resp = requests.get(
                    self.base_url,
                    params={"access_token": self.access_token},
                    stream=True,
                    timeout=30,
                )
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if not line:
                        continue

                    if not line.startswith(b"data:"):
                        continue

                    if b"message.new" not in line:
                        continue

                    try:
                        data = line.decode("utf-8").split("data:")[1].strip()
                        raw = json.loads(data)
                    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as e:
                        logger.debug(f"Failed to parse line: {e}")
                        continue

                    event = self.normalize_event(raw)
                    if not event:
                        continue

                    await self.emit(event)

                    logger.info(
                        f"EVENT [{event.source.name}] {event.sender.name}: {event.content.text[:30]}..."
                    )

            except requests.exceptions.RequestException as e:
                logger.error(f"WeFlowSource connection error: {e}")
                self.health.reconnect_count += 1
                time.sleep(5)

            except Exception as e:
                logger.error(f"WeFlowSource error: {e}")
                self.health.reconnect_count += 1
                time.sleep(5)

        logger.info("WeFlowSource stopped")
