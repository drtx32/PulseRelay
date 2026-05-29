"""
Slack source adapter.

Consumes Slack events via Socket Mode (WebSocket) and publishes normalized
EventEnvelope objects into the EventBus.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web import WebClient

from core.event import EventEnvelope, EventContent, EventContext, EventMeta, EventSender, EventSource
from core.source_adapter import (
    SourceAdapter,
    SourceCapabilities,
    SourceManifest,
)

logger = logging.getLogger(__name__)


# Mapping of Slack event types to PulseRelay event types
EVENT_TYPE_MAP: dict[str, str] = {
    "message": "message.created",
    "message.channels": "message.created",
    "message.groups": "message.created",
    "message.im": "message.created",
    "message.mpim": "message.created",
    "app_mention": "mention.received",
    "app_home_opened": "app_home.opened",
    "app_uninstalled": "app.uninstalled",
    "member_joined_channel": "member.joined",
    "member_left_channel": "member.left",
    "reaction_added": "reaction.added",
    "reaction_removed": "reaction.removed",
    "channel_created": "channel.created",
    "channel_deleted": "channel.deleted",
    "channel_rename": "channel.renamed",
    "channel_archive": "channel.archived",
    "channel_unarchive": "channel.unarchived",
    "emoji_changed": "emoji.changed",
    "pin_added": "pin.added",
    "pin_removed": "pin.removed",
    "workflow_step_execute": "workflow.step_executed",
}


def _map_event_type(slack_event_type: str) -> str:
    """Map Slack event type to PulseRelay event type."""
    return EVENT_TYPE_MAP.get(slack_event_type, slack_event_type)


def _extract_text(event_data: dict[str, Any]) -> str:
    """Extract text content from a Slack event."""
    # Regular message
    if "text" in event_data:
        return event_data["text"]

    # Block kit content
    if "blocks" in event_data:
        texts = []
        for block in event_data.get("blocks", []):
            if block.get("type") == "rich_text":
                for element in block.get("elements", []):
                    if isinstance(element, dict) and element.get("type") == "text":
                        texts.append(element.get("text", ""))
                    elif isinstance(element, dict) and element.get("type") == "type":
                        texts.append(element.get("text", ""))
        if texts:
            return "\n".join(texts)

    return ""


def _get_channel_name(event_data: dict[str, Any], client: WebClient) -> str:
    """Get the channel name from event data."""
    channel_id = event_data.get("channel", "")
    if not channel_id:
        return ""

    # Try to get from cache first
    if channel_id.startswith("C") or channel_id.startswith("G"):
        try:
            response = client.conversations_info(channel=channel_id)
            if response.get("ok"):
                return response["channel"].get("name", channel_id)
        except Exception:
            pass

    return channel_id


def _get_sender(event_data: dict[str, Any]) -> EventSender:
    """Extract sender information from a Slack event."""
    user_id = event_data.get("user", "")
    if not user_id:
        return EventSender(id="unknown", name="Unknown", trust_level="unknown")

    # Map bot users
    if event_data.get("bot_id"):
        return EventSender(
            id=event_data["bot_id"],
            name=event_data.get("username", f"Bot:{event_data['bot_id']}"),
            trust_level="bot",
        )

    return EventSender(
        id=user_id,
        name=user_id,  # Will be enriched by user info if available
        trust_level="user",
    )


class SlackSource(SourceAdapter):
    """Slack Socket Mode source adapter."""

    manifest = SourceManifest(
        id="slack",
        name="Slack Source",
        description="Consume Slack events via Socket Mode (WebSocket) and normalize events.",
        capabilities=SourceCapabilities(
            supports_websocket=True,
            supports_webhook=False,
            supports_streaming=True,
            supports_ack=True,
        ),
        tags=["slack", "socket_mode", "websocket", "streaming"],
    )

    def __init__(
        self,
        event_bus,
        app_token: str,
        bot_token: str,
        allowed_channels: list[str] | None = None,
        enabled: bool = True,
    ):
        """
        Initialize Slack source adapter.

        Args:
            event_bus: The event bus to publish events to.
            app_token: Slack App-Level Token (xapp-...).
            bot_token: Slack Bot User OAuth Access Token (xoxb-...).
            allowed_channels: Optional list of channel IDs to filter events.
            enabled: Whether the source is active.
        """
        super().__init__(event_bus=event_bus, enabled=enabled)

        self.app_token = app_token
        self.bot_token = bot_token
        self.allowed_channels = set(allowed_channels) if allowed_channels else None
        self._client: SocketModeClient | None = None
        self._web_client: WebClient | None = None

    def normalize_event(self, event_data: dict[str, Any], event_type: str) -> EventEnvelope | None:
        """Normalize a Slack event into EventEnvelope."""

        # Get channel info
        channel_id = event_data.get("channel", "")
        channel_type = event_data.get("channel_type", "")

        # Skip messages from disallowed channels
        if self.allowed_channels and channel_id not in self.allowed_channels:
            logger.debug(f"Ignored event from unauthorized channel: {channel_id}")
            return None

        # Build dedupe key
        ts = event_data.get("ts", "")
        thread_ts = event_data.get("thread_ts", "")
        dedupe_key = f"slack:{channel_id}:{ts}"
        if thread_ts:
            dedupe_key = f"slack:{channel_id}:{thread_ts}:{ts}"

        # Determine final event type
        mapped_type = _map_event_type(event_type)

        # Extract text content
        text = _extract_text(event_data)

        # Get sender
        sender = _get_sender(event_data)

        return EventEnvelope(
            source=EventSource(
                type="slack",
                id=channel_id,
                name=channel_id,
            ),
            sender=sender,
            event=EventMeta(
                type=mapped_type,
                timestamp=datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat(),
                dedupe_key=dedupe_key,
            ),
            content=EventContent(
                title=f"Slack {channel_type or 'message'}" if channel_type else "Slack message",
                text=text,
                raw=event_data,
            ),
            context=EventContext(
                conversation_id=channel_id,
                channel_id=channel_id,
                thread_id=thread_ts or "",
                extra={
                    "channel": channel_id,
                    "channel_type": channel_type,
                    "team_id": event_data.get("team", ""),
                    "thread_ts": thread_ts,
                    "event_ts": ts,
                    "bot_id": event_data.get("bot_id"),
                },
            ),
        )

    async def _handle_socket_mode_request(self, client: SocketModeClient, request: SocketModeRequest):
        """Handle incoming Socket Mode requests."""
        try:
            if request.type == "events_api":
                # Acknowledge the event
                client.send_socket_mode_response(request)

                # Process the event payload
                event_data = request.payload.get("event", {})
                event_type = event_data.get("type", "")

                # Skip bot messages if needed (can add bot filtering here)
                if event_data.get("bot_id") and event_data.get("subtype") == "bot_message":
                    logger.debug("Skipping bot message event")
                    return

                event = self.normalize_event(event_data, event_type)
                if not event:
                    return

                await self.emit(event)

                logger.info(
                    f"SLACK [{event.source.id}] {event.sender.name}: {event.content.text[:50] if event.content.text else '(no text)'}..."
                )

            elif request.type == "interactive":
                # Handle interactive payloads (modals, components, etc.)
                client.send_socket_mode_response(request)

                payload = request.payload
                event = EventEnvelope(
                    source=EventSource(type="slack", id=payload.get("channel", {}).get("id", "") if isinstance(payload.get("channel"), dict) else payload.get("channel", {}).get("id", ""), name="slack"),
                    sender=_get_sender(payload.get("user", {})),
                    event=EventMeta(
                        type="interactive.action",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        dedupe_key=f"slack:interactive:{payload.get('action_ts', payload.get('callback_id', ''))}",
                    ),
                    content=EventContent(
                        title="Slack Interactive",
                        text=payload.get("message", {}).get("text", "Interactive payload"),
                        raw=payload,
                    ),
                    context=EventContext(
                        conversation_id=payload.get("channel", {}).get("id", "") if isinstance(payload.get("channel"), dict) else "",
                        extra={"trigger_id": payload.get("trigger_id"), "callback_id": payload.get("callback_id")},
                    ),
                )
                await self.emit(event)
                logger.info(f"SLACK interactive: {payload.get('type', 'unknown')}")

            elif request.type == "commands":
                # Handle slash commands
                client.send_socket_mode_response(request)

                payload = request.payload
                event = EventEnvelope(
                    source=EventSource(type="slack", id=payload.get("channel_id", ""), name="slack"),
                    sender=EventSender(id=payload.get("user_id", ""), name=payload.get("user_name", payload.get("user_id", "")), trust_level="user"),
                    event=EventMeta(
                        type="command.executed",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        dedupe_key=f"slack:command:{payload.get('command', '')}:{payload.get('trigger_id', '')}",
                    ),
                    content=EventContent(
                        title=f"Slack Command: {payload.get('command', '')}",
                        text=payload.get("text", ""),
                        raw=payload,
                    ),
                    context=EventContext(
                        conversation_id=payload.get("channel_id", ""),
                        extra={"command": payload.get("command"), "trigger_id": payload.get("trigger_id")},
                    ),
                )
                await self.emit(event)
                logger.info(f"SLACK command: {payload.get('command', '')}")

        except Exception as e:
            logger.error(f"Error handling Slack Socket Mode request: {e}")
            self.health.last_error = str(e)

    async def run(self):
        """Main event loop for the Slack source adapter."""
        logger.info("SlackSource starting")

        self._web_client = WebClient(token=self.bot_token)
        self._client = SocketModeClient(
            app_token=self.app_token,
            web_client=self._web_client,
            auto_reconnect=True,
            send_frequency=1,
        )

        self._client.socket_mode_request_listeners.append(self._handle_socket_mode_request)

        # Start the Socket Mode client
        await self._client.connect()

        logger.info("SlackSource connected and listening for events")

        # Keep the connection alive
        # SocketModeClient handles reconnection automatically
        while self._client.is_connected():
            await self._client.send_ping()
            import asyncio
            await asyncio.sleep(30)

        logger.info("SlackSource stopped")

    async def stop(self):
        """Stop the Slack source adapter."""
        await super().stop()
        if self._client:
            self._client.disconnect()
        logger.info("SlackSource shutdown complete")
