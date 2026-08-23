"""Process-local business event bus.

Transport adapters (browser WebSocket/SSE, logs, metrics) subscribe to this
bus, but event ingestion and routing never depend on any one transport.
"""
from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.max_queue_size = max_queue_size

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow UI observer must never block ingestion or delivery.
                self._subscribers.discard(queue)
