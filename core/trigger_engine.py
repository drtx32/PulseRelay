"""
Trigger Engine - 消息聚合 + 触发判断
集成到 Signals，通过统一 queue 消费消息
"""

import time
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class TriggerConfig:
    content_threshold: int = 1000
    message_threshold: int = 10
    idle_timeout: float = 20.0
    min_trigger_interval: float = 5.0


@dataclass
class Message:
    chat: str
    sender: str
    content: str
    time: str
    local_id: int
    chat_name: str = ""
    raw: dict = field(default_factory=dict)

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


class TriggerResult:
    def __init__(self, triggered: bool = False, reason: str = "", messages: list = None):
        self.triggered = triggered
        self.reason = reason
        self.messages = messages or []


class TriggerEngine:
    """
    消息聚合 + 触发判断
    从 Signals.queue 消费消息，不自己管理 queue
    """

    def __init__(self, signals, config: TriggerConfig = None, monitor_chats: list[str] = None):
        self.signals = signals
        self.config = config or TriggerConfig()
        self.monitor_chats = monitor_chats or []

        self.messages: list[Message] = []
        self.seen_keys: set[tuple] = set()
        self.last_add_time: float = 0  # 0 表示还没有消息
        self.last_trigger_time: float = 0

    def consume_signals(self, timeout: float = 0.5) -> Optional[dict]:
        """从 Signals.queue 消费一条消息"""
        try:
            key, raw = self.signals.queue.get(timeout=timeout)
            return key, raw
        except:
            return None

    def process_raw(self, raw: dict):
        """处理原始消息"""
        msg = Message.from_dict(raw)

        # 检查监控列表（支持 chat 或 chat_name）
        if self.monitor_chats:
            chat_name = raw.get("chat_name", "")
            if msg.chat not in self.monitor_chats and chat_name not in self.monitor_chats:
                return False

        # 去重
        if msg.local_id != 0:
            key = (msg.chat, msg.local_id)
            if key in self.seen_keys:
                print(f"  [去重] {key}")
                return False
            self.seen_keys.add(key)

        self.messages.append(msg)
        self.last_add_time = time.monotonic()
        return True

    def check_trigger(self) -> TriggerResult:
        """检查触发条件"""
        now = time.monotonic()

        # 检查最小触发间隔
        if now - self.last_trigger_time < self.config.min_trigger_interval:
            return TriggerResult(False)

        if not self.messages:
            return TriggerResult(False)

        total_chars = sum(len(m.content) for m in self.messages)
        reasons = []

        if total_chars >= self.config.content_threshold:
            reasons.append(f"内容超限: {total_chars}/{self.config.content_threshold}字符")

        if len(self.messages) >= self.config.message_threshold:
            reasons.append(f"消息超限: {len(self.messages)}/{self.config.message_threshold}条")

        idle_time = now - self.last_add_time if self.last_add_time > 0 else 0
        if idle_time >= self.config.idle_timeout:
            reasons.append(f"空闲超时: {idle_time:.1f}秒无新消息")

        if reasons:
            self.last_trigger_time = now
            return TriggerResult(True, "; ".join(reasons), list(self.messages))

        return TriggerResult(False)

    def reset(self):
        """重置状态"""
        self.messages = []
        self.seen_keys.clear()
        self.last_add_time = 0  # 0 表示等待消息中，不参与 idle 计算
        self.last_trigger_time = time.monotonic()

    def get_stats(self) -> dict:
        """获取当前统计"""
        idle_seconds = 0
        if self.messages and self.last_add_time > 0:
            idle_seconds = time.monotonic() - self.last_add_time
        return {
            "message_count": len(self.messages),
            "total_chars": sum(len(m.content) for m in self.messages),
            "idle_seconds": idle_seconds,
            "seen_keys": len(self.seen_keys),
            "waiting_for_message": len(self.messages) == 0,
        }
