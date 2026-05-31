"""
Event primitives for PulseRelay.

This module introduces the event-native abstraction that will gradually replace
raw message dictionaries across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

RiskLevel = Literal["read", "write", "deploy", "payment", "admin"]
Priority = Literal["low", "normal", "high", "urgent"]
TrustLevel = Literal["system", "admin", "user", "unknown"]


@dataclass
class EventSource:
    """Where an event comes from."""

    type: str
    id: str = ""
    tenant_id: str = ""
    name: str = ""


@dataclass
class EventSender:
    """Who or what emitted the event."""

    id: str = ""
    name: str = ""
    email: str = ""
    role: str = ""
    trust_level: TrustLevel = "unknown"


@dataclass
class EventMeta:
    """Platform-independent event metadata."""

    type: str
    action: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dedupe_key: str = ""


@dataclass
class EventContent:
    """Human-readable and raw event content."""

    title: str = ""
    text: str = ""
    html: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventContext:
    """Context that helps route or execute the event."""

    conversation_id: str = ""
    project_id: str = ""
    repo: str = ""
    channel_id: str = ""
    thread_id: str = ""
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventPermissions:
    """Policy hints attached to an event."""

    allowed_tools: list[str] = field(default_factory=list)
    allowed_agents: list[str] = field(default_factory=list)
    requires_approval: bool = False
    max_risk_level: RiskLevel = "read"


@dataclass
class EventRouting:
    """Routing hints produced by sources or routers."""

    priority: Priority = "normal"
    labels: list[str] = field(default_factory=list)
    target_agent: str = ""


@dataclass
class EventEnvelope:
    """Normalized event envelope used inside PulseRelay."""

    id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    source: EventSource = field(default_factory=lambda: EventSource(type="unknown"))
    sender: EventSender = field(default_factory=EventSender)
    event: EventMeta = field(default_factory=lambda: EventMeta(type="unknown"))
    content: EventContent = field(default_factory=EventContent)
    context: EventContext = field(default_factory=EventContext)
    permissions: EventPermissions = field(default_factory=EventPermissions)
    routing: EventRouting = field(default_factory=EventRouting)

    @property
    def dedupe_key(self) -> str:
        if self.event.dedupe_key:
            return self.event.dedupe_key
        return f"{self.source.type}:{self.source.id}:{self.event.type}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source.__dict__,
            "sender": self.sender.__dict__,
            "event": self.event.__dict__,
            "content": self.content.__dict__,
            "context": self.context.__dict__,
            "permissions": self.permissions.__dict__,
            "routing": self.routing.__dict__,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        """Rehydrate an EventEnvelope from a serialized dictionary."""

        source_data = data.get("source", {}) if isinstance(data, dict) else {}
        sender_data = data.get("sender", {}) if isinstance(data, dict) else {}
        event_data = data.get("event", {}) if isinstance(data, dict) else {}
        content_data = data.get("content", {}) if isinstance(data, dict) else {}
        context_data = data.get("context", {}) if isinstance(data, dict) else {}
        permissions_data = data.get("permissions", {}) if isinstance(data, dict) else {}
        routing_data = data.get("routing", {}) if isinstance(data, dict) else {}

        return cls(
            id=data.get("id", f"evt_{uuid4().hex}"),
            source=EventSource(**source_data),
            sender=EventSender(**sender_data),
            event=EventMeta(**event_data),
            content=EventContent(**content_data),
            context=EventContext(**context_data),
            permissions=EventPermissions(**permissions_data),
            routing=EventRouting(**routing_data),
        )

    @classmethod
    def from_message_dict(cls, raw: dict[str, Any], source_type: str = "message") -> "EventEnvelope":
        """Create an event from the current message-shaped dictionaries.

        This compatibility method allows the existing TriggerEngine and source
        modules to migrate incrementally.
        """

        chat = str(raw.get("chat", ""))
        chat_name = str(raw.get("chat_name", ""))
        sender = str(raw.get("sender", ""))
        local_id = raw.get("local_id", "")
        timestamp = str(raw.get("time", "")) or datetime.now(timezone.utc).isoformat()

        dedupe_key = ""
        if chat and local_id:
            dedupe_key = f"{source_type}:{chat}:{local_id}"

        return cls(
            source=EventSource(type=source_type, id=chat, name=chat_name),
            sender=EventSender(id=sender, name=sender, trust_level="user" if sender else "unknown"),
            event=EventMeta(type="message.created", timestamp=timestamp, dedupe_key=dedupe_key),
            content=EventContent(
                title=chat_name or chat,
                text=str(raw.get("content", "")),
                raw=raw,
            ),
            context=EventContext(conversation_id=chat, channel_id=chat, extra={"chat_name": chat_name}),
        )


def ensure_event(value: EventEnvelope | dict[str, Any], source_type: str = "message") -> EventEnvelope:
    """Normalize an existing event or message dictionary into EventEnvelope."""

    if isinstance(value, EventEnvelope):
        return value
    if (
        isinstance(value, dict)
        and "source" in value
        and "event" in value
        and "content" in value
    ):
        return EventEnvelope.from_dict(value)
    return EventEnvelope.from_message_dict(value, source_type=source_type)
