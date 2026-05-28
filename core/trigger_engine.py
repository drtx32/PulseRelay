"""
Trigger Engine - event aggregation and trigger decisions.

This module remains compatible with the original message-centric flow while
processing normalized EventEnvelope objects internally.
"""

import time
from typing import Optional
from dataclasses import dataclass

from core.event import EventEnvelope, ensure_event


@dataclass
class TriggerConfig:
    content_threshold: int = 1000
    message_threshold: int = 10
    idle_timeout: float = 20.0
    min_trigger_interval: float = 5.0


@dataclass
class Message:
    """Compatibility view over a legacy message-shaped dictionary."""

    chat: str
    sender: str
    content: str
    time: str
    local_id: int
    chat_name: str = ""
    raw: dict = None

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            chat=d.get("chat", ""),
            chat_name=d.get("chat_name", ""),
            sender=d.get("sender", ""),
            content=d.get("content", ""),
            time=d.get("time", ""),
            local_id=d.get("local_id", 0),
            raw=d,
        )

    @classmethod
    def from_event(cls, event: EventEnvelope) -> "Message":
        raw = event.content.raw or event.to_dict()
        return cls(
            chat=event.context.conversation_id or event.source.id,
            chat_name=event.source.name or event.content.title,
            sender=event.sender.name or event.sender.id,
            content=event.content.text,
            time=event.event.timestamp,
            local_id=raw.get("local_id", 0) if isinstance(raw, dict) else 0,
            raw=raw if isinstance(raw, dict) else event.to_dict(),
        )


class TriggerResult:
    def __init__(
        self,
        triggered: bool = False,
        reason: str = "",
        events: list[EventEnvelope] = None,
        messages: list[Message] = None,
    ):
        self.triggered = triggered
        self.reason = reason
        self.events = events or []
        self.messages = messages or [Message.from_event(event) for event in self.events]


class TriggerEngine:
    """
    Event aggregation + trigger decisions.

    The public API keeps process_raw() and messages compatibility so existing
    gateway code can migrate incrementally.
    """

    def __init__(self, signals, config: TriggerConfig = None, monitor_chats: list[str] = None):
        self.signals = signals
        self.config = config or TriggerConfig()
        self.monitor_chats = monitor_chats or []

        self.events: list[EventEnvelope] = []
        self.seen_keys: set[str] = set()
        self.last_add_time: float = 0
        self.last_trigger_time: float = 0

    @property
    def messages(self) -> list[Message]:
        """Compatibility projection for existing message formatting code."""
        return [Message.from_event(event) for event in self.events]

    def consume_signals(self, timeout: float = 0.5) -> Optional[tuple]:
        """Consume one queued item from Signals.queue or EventBus.queue."""
        try:
            item = self.signals.queue.get(timeout=timeout)
            if hasattr(item, "event") and hasattr(item, "key"):
                return item.key, item.event
            key, raw = item
            return key, raw
        except Exception:
            return None

    def process_raw(self, raw: dict, source_type: str = "message"):
        """Compatibility wrapper for old source modules."""
        return self.process_event(ensure_event(raw, source_type=source_type))

    def process_event(self, event: EventEnvelope):
        """Process a normalized event."""
        if self.monitor_chats:
            chat = event.context.conversation_id or event.source.id
            chat_name = event.source.name or event.context.extra.get("chat_name", "")
            if chat not in self.monitor_chats and chat_name not in self.monitor_chats:
                return False

        dedupe_key = event.dedupe_key
        if dedupe_key in self.seen_keys:
            print(f"  [去重] {dedupe_key}")
            return False
        self.seen_keys.add(dedupe_key)

        self.events.append(event)
        self.last_add_time = time.monotonic()
        return True

    def check_trigger(self) -> TriggerResult:
        """Check whether the current event batch should trigger downstream work."""
        now = time.monotonic()

        if now - self.last_trigger_time < self.config.min_trigger_interval:
            return TriggerResult(False)

        if not self.events:
            return TriggerResult(False)

        total_chars = sum(len(event.content.text) for event in self.events)
        reasons = []

        if total_chars >= self.config.content_threshold:
            reasons.append(f"内容超限: {total_chars}/{self.config.content_threshold}字符")

        if len(self.events) >= self.config.message_threshold:
            reasons.append(f"消息超限: {len(self.events)}/{self.config.message_threshold}条")

        idle_time = now - self.last_add_time if self.last_add_time > 0 else 0
        if idle_time >= self.config.idle_timeout:
            reasons.append(f"空闲超时: {idle_time:.1f}秒无新消息")

        if reasons:
            self.last_trigger_time = now
            return TriggerResult(True, "; ".join(reasons), events=list(self.events))

        return TriggerResult(False)

    def reset(self):
        """Reset the current aggregation batch."""
        self.events = []
        self.seen_keys.clear()
        self.last_add_time = 0
        self.last_trigger_time = time.monotonic()

    def get_stats(self) -> dict:
        """Return current aggregation stats."""
        idle_seconds = 0
        if self.events and self.last_add_time > 0:
            idle_seconds = time.monotonic() - self.last_add_time
        return {
            "event_count": len(self.events),
            "message_count": len(self.events),
            "total_chars": sum(len(event.content.text) for event in self.events),
            "idle_seconds": idle_seconds,
            "seen_keys": len(self.seen_keys),
            "waiting_for_message": len(self.events) == 0,
        }
