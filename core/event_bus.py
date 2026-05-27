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

    def __init__(self):
        self.queue: queue.SimpleQueue[EventRecord] = queue.SimpleQueue()

    def put(self, key: str, value) -> EventEnvelope:
        event = ensure_event(value, source_type=key)
        self.queue.put(EventRecord(key=key, event=event))
        return event

    def get(self, timeout: Optional[float] = None) -> EventRecord:
        return self.queue.get(timeout=timeout)
