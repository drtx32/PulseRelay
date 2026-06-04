"""
Lark/Feishu webhook source.

This source receives Open Platform event callbacks through FastAPI and adapts
`im.message.receive_v1` events into PulseRelay's aggregated Message shape.
"""

import json
import logging
from datetime import datetime
from typing import Any

from base import Module, Signals

logger = logging.getLogger(__name__)


class LarkWebhookSource(Module):
    """飞书/Lark 事件回调源，通过 webhook 接收消息事件。"""

    def __init__(
        self,
        signals: Signals = None,
        verification_token: str = "",
        encrypt_key: str = "",
        include_non_text: bool = True,
        enabled: bool = True,
    ):
        super().__init__(signals, enabled)
        self.verification_token = verification_token or ""
        self.encrypt_key = encrypt_key or ""
        self.include_non_text = include_non_text

    async def run(self):
        """Webhook 源不需要后台连接；FastAPI 路由会直接调用 handle_event。"""
        logger.info("LarkWebhookSource ready for callback events")

    def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """验证并处理 Lark/Feishu 回调 payload。"""
        if "encrypt" in payload:
            logger.warning("Encrypted Lark callbacks are not supported yet")
            return {
                "ok": False,
                "error": "encrypted_lark_callback_not_supported",
            }

        if self._is_url_verification(payload):
            if not self._verify_token(payload):
                return {"ok": False, "error": "invalid_lark_verification_token"}
            return {"challenge": payload.get("challenge", "")}

        if not self._verify_token(payload):
            return {"ok": False, "error": "invalid_lark_verification_token"}

        event_type = self._get_event_type(payload)
        if event_type not in {"im.message.receive_v1", "message"}:
            logger.debug(f"Ignored Lark event type: {event_type}")
            return {"ok": True, "ignored": True, "event_type": event_type}

        message = self._adapt_message(payload)
        if not message:
            return {"ok": True, "ignored": True, "event_type": event_type}

        self.signals.put("lark_message", message)
        logger.info(
            f"LARK [{message['chat_name']}] {message['sender']}: "
            f"{message['content'][:30]}..."
        )
        return {"ok": True, "queued": True, "event_type": event_type}

    def _is_url_verification(self, payload: dict[str, Any]) -> bool:
        return payload.get("type") == "url_verification" and "challenge" in payload

    def _verify_token(self, payload: dict[str, Any]) -> bool:
        if not self.verification_token:
            return True

        token = payload.get("token")
        if not token:
            token = payload.get("header", {}).get("token")
        return token == self.verification_token

    def _get_event_type(self, payload: dict[str, Any]) -> str:
        return (
            payload.get("header", {}).get("event_type")
            or payload.get("event", {}).get("type")
            or payload.get("type", "")
        )

    def _adapt_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event = payload.get("event", {})
        message = event.get("message", {})
        if not message and payload.get("type") == "event_callback":
            return self._adapt_v1_message(payload)

        message_type = message.get("message_type", "")
        content = self._extract_content(message_type, message.get("content", ""))
        if not content:
            return None
        if message_type != "text" and not self.include_non_text:
            return None

        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {})
        sender_name = (
            sender_id.get("open_id")
            or sender_id.get("user_id")
            or sender_id.get("union_id")
            or sender.get("sender_type")
            or "unknown"
        )

        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")
        create_time = message.get("create_time") or payload.get("header", {}).get("create_time")
        display_time = self._format_time(create_time)

        return {
            "source": "lark",
            "platform": "lark",
            "local_id": message.get("message_id") or payload.get("header", {}).get("event_id") or create_time,
            "chat": chat_id,
            "chat_name": chat_id or chat_type or "lark",
            "sender": sender_name,
            "content": content,
            "time": display_time,
            "raw": payload,
        }

    def _adapt_v1_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event = payload.get("event", {})
        message_type = event.get("msg_type", "")
        content = event.get("text", "") if message_type == "text" else f"[{message_type}]"
        if not content or (message_type != "text" and not self.include_non_text):
            return None

        chat_id = event.get("open_chat_id", "")
        return {
            "source": "lark",
            "platform": "lark",
            "local_id": payload.get("uuid") or event.get("msg_id") or payload.get("ts"),
            "chat": chat_id,
            "chat_name": chat_id or event.get("chat_type", "") or "lark",
            "sender": event.get("open_id", "unknown"),
            "content": content,
            "time": self._format_time(payload.get("ts")),
            "raw": payload,
        }

    def _extract_content(self, message_type: str, raw_content: str | dict[str, Any]) -> str:
        content = raw_content
        if isinstance(raw_content, str):
            try:
                content = json.loads(raw_content) if raw_content else {}
            except json.JSONDecodeError:
                content = {"text": raw_content}

        if not isinstance(content, dict):
            return f"[{message_type}]"

        if message_type == "text":
            return content.get("text", "")
        if message_type == "post":
            return self._extract_post_content(content)
        if message_type:
            return f"[{message_type}]"
        return content.get("text", "")

    def _extract_post_content(self, content: dict[str, Any]) -> str:
        zh_cn = content.get("zh_cn") or content.get("en_us") or content
        parts: list[str] = []
        title = zh_cn.get("title")
        if title:
            parts.append(str(title))

        for line in zh_cn.get("content", []):
            line_parts: list[str] = []
            for item in line:
                text = item.get("text") or item.get("name") or item.get("href")
                if text:
                    line_parts.append(str(text))
            if line_parts:
                parts.append("".join(line_parts))
        return "\n".join(parts)

    def _format_time(self, raw_time: Any) -> str:
        try:
            timestamp = int(raw_time)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return datetime.now().strftime("%m-%d %H:%M")
