"""
Telegram source adapter.

Consumes Telegram Bot API updates and publishes normalized EventEnvelope
objects into the EventBus.
"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update, Message, Chat, User
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from core.event import EventEnvelope, EventContent, EventContext, EventMeta, EventSender, EventSource
from core.source_adapter import (
    SourceAdapter,
    SourceCapabilities,
    SourceManifest,
)

logger = logging.getLogger(__name__)


def _extract_text(update: Update) -> str:
    """Extract text content from a Telegram update."""
    if update.message:
        return update.message.text or ""
    if update.edited_message:
        return update.edited_message.text or ""
    if update.callback_query:
        return update.callback_query.data or ""
    return ""


def _get_chat_id(update: Update) -> str:
    """Get the chat ID from an update."""
    if update.message:
        return str(update.message.chat.id)
    if update.edited_message:
        return str(update.edited_message.chat.id)
    if update.callback_query and update.callback_query.message:
        return str(update.callback_query.message.chat.id)
    if update.my_chat_member:
        return str(update.my_chat_member.chat.id)
    return ""


def _get_sender(update: Update) -> EventSender:
    """Extract sender information from a Telegram update."""
    user: User | None = None
    if update.message:
        user = update.message.from_user
    elif update.edited_message:
        user = update.edited_message.from_user
    elif update.callback_query:
        user = update.callback_query.from_user

    if user:
        trust = "user"
        if user.is_bot:
            trust = "bot"
        return EventSender(
            id=str(user.id),
            name=user.full_name or user.username or str(user.id),
            trust_level=trust,
        )
    return EventSender(id="unknown", name="Unknown", trust_level="user")


def _get_chat(update: Update) -> tuple[str, str]:
    """Get chat ID and name from an update."""
    chat: Chat | None = None
    if update.message:
        chat = update.message.chat
    elif update.edited_message:
        chat = update.edited_message.chat
    elif update.callback_query and update.callback_query.message:
        chat = update.callback_query.message.chat
    elif update.my_chat_member:
        chat = update.my_chat_member.chat

    if chat:
        return str(chat.id), chat.title or chat.username or str(chat.id)
    return "", ""


def _determine_event_type(update: Update) -> str:
    """Determine the event type from a Telegram update."""
    if update.message:
        if update.message.new_chat_members:
            return "member.joined"
        if update.message.left_chat_member:
            return "member.left"
        if update.message.text:
            return "message.created"
        if update.message.photo:
            return "message.photo"
        if update.message.document:
            return "message.document"
        if update.message.video:
            return "message.video"
        if update.message.voice:
            return "message.voice"
        return "message.created"
    if update.edited_message:
        return "message.edited"
    if update.callback_query:
        return "callback.query"
    if update.my_chat_member:
        return "chat.member.updated"
    return "update.received"


class TelegramSource(SourceAdapter):
    """Telegram Bot API source adapter."""

    manifest = SourceManifest(
        id="telegram",
        name="Telegram Source",
        description="Consume Telegram Bot API updates and normalize events.",
        capabilities=SourceCapabilities(
            supports_streaming=True,
            supports_webhook=False,
            supports_ack=True,
        ),
        tags=["telegram", "bot", "streaming"],
    )

    def __init__(
        self,
        event_bus,
        bot_token: str,
        allowed_chat_ids: list[str] | None = None,
        enabled: bool = True,
    ):
        """
        Initialize Telegram source adapter.

        Args:
            event_bus: The event bus to publish events to.
            bot_token: Telegram bot token from @BotFather.
            allowed_chat_ids: Optional list of chat IDs to filter events.
            enabled: Whether the source is active.
        """
        super().__init__(event_bus=event_bus, enabled=enabled)

        self.bot_token = bot_token
        self.allowed_chat_ids = set(allowed_chat_ids) if allowed_chat_ids else None
        self._application: Application | None = None

    def normalize_event(self, update: Update) -> EventEnvelope | None:
        """Normalize a Telegram update into EventEnvelope."""

        chat_id, chat_name = _get_chat(update)
        sender = _get_sender(update)
        event_type = _determine_event_type(update)
        text = _extract_text(update)
        message_id = ""

        if update.message:
            message_id = str(update.message.message_id)
        elif update.edited_message:
            message_id = str(update.edited_message.message_id)
        elif update.callback_query:
            message_id = str(update.callback_query.inline_message_id) or ""

        dedupe_key = f"telegram:{chat_id}:{message_id}" if message_id else f"telegram:{chat_id}"

        return EventEnvelope(
            source=EventSource(
                type="telegram",
                id=chat_id,
                name=chat_name,
            ),
            sender=sender,
            event=EventMeta(
                type=event_type,
                timestamp=datetime.now().isoformat(),
                dedupe_key=dedupe_key,
            ),
            content=EventContent(
                title=chat_name,
                text=text,
                raw=update.to_dict(),
            ),
            context=EventContext(
                conversation_id=chat_id,
                channel_id=chat_id,
                extra={
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "message_id": message_id,
                },
            ),
        )

    async def _handle_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming Telegram updates."""
        try:
            chat_id = _get_chat_id(update)

            if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
                logger.debug(f"Ignored update from unauthorized chat: {chat_id}")
                return

            event = self.normalize_event(update)
            if not event:
                return

            await self.emit(event)

            logger.info(
                f"TELEGRAM [{event.source.name}] {event.sender.name}: {event.content.text[:30]}..."
            )
        except Exception as e:
            logger.error(f"Error handling Telegram update: {e}")

    async def run(self):
        """Main event loop for the Telegram source adapter."""
        logger.info("TelegramSource starting")

        self._application = (
            Application.builder()
            .token(self.bot_token)
            .build()
        )

        self._application.add_handler(
            MessageHandler(filters.ALL, self._handle_update),
        )
        self._application.add_handler(
            CommandHandler("start", self._handle_update),
        )
        self._application.add_handler(
            CommandHandler("help", self._handle_update),
        )

        async with self._application:
            await self._application.start()
            await self._application.run_polling(
                allowed_updates=Update.ALL_UPDATES,
            )

        logger.info("TelegramSource stopped")

    async def stop(self):
        """Stop the Telegram source adapter."""
        await super().stop()
        if self._application:
            await self._application.stop()
        logger.info("TelegramSource shutdown complete")
