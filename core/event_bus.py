"""
Event bus abstraction.

This module keeps compatibility with the existing Signals queue while
introducing event-native terminology.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Optional

from core.event import EventEnvelope, ensure_event
from core.persistence import SQLitePhase9Store


@dataclass
class EventRecord:
    """A queue record stored inside the event bus."""

    key: str
    event: EventEnvelope


class EventBus:
    """Simple in-memory event bus.

    The current implementation intentionally mirrors the existing Signals.queue
    behavior so migration can happen incrementally.
    """

    def __init__(self, store: SQLitePhase9Store | None = None):
        self.queue: queue.SimpleQueue[EventRecord] = queue.SimpleQueue()
        self.store = store

    def attach_store(self, store: SQLitePhase9Store | None) -> None:
        self.store = store

    def put(self, key: str, value) -> EventEnvelope:
        event = ensure_event(value, source_type=key)
        self.queue.put(EventRecord(key=key, event=event))
        if self.store is not None:
            self.store.record_event(bus_key=key, event=event, status="received")
        return event

    def get(self, timeout: Optional[float] = None) -> EventRecord:
        return self.queue.get(timeout=timeout)

    def replay(
        self,
        limit: int = 100,
        source_type: str | None = None,
        event_type: str | None = None,
        status: str | None = "received",
    ) -> int:
        if self.store is None:
            return 0

        events = self.store.replay_events(
            limit=limit,
            source_type=source_type,
            event_type=event_type,
            status=status,
        )
        for event in events:
            self.queue.put(EventRecord(key=event.source.type or "replay", event=event))
        return len(events)
