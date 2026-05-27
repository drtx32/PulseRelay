"""
WeFlow 数据源 - 通过 HTTP SSE 流消费微信消息
"""

import json
import logging
import os
import time
import requests
from datetime import datetime
from base import Module, Signals

logger = logging.getLogger(__name__)


class WeFlowSource(Module):
    """微信数据源，通过 HTTP SSE 流消费消息"""

    def __init__(
        self,
        signals: Signals = None,
        host: str = "localhost",
        port: int = 5031,
        access_token: str = None,
        enabled: bool = True
    ):
        super().__init__(signals, enabled)
        self.host = host
        self.port = port
        self.access_token = access_token or os.getenv(
            "WEFLOW_TOKEN", "3bbdf1d0ed8ec3cd357894a9bdb99494")
        self.base_url = f"http://{self.host}:{self.port}/api/v1/push/messages"

    async def run(self):
        logger.info(f"WeFlowSource starting: {self.base_url}")

        while not self.signals.terminate:
            try:
                resp = requests.get(
                    self.base_url,
                    params={"access_token": self.access_token},
                    stream=True,
                    timeout=30
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

                    # 字段适配：raw → Message 格式
                    # 去重用 timestamp + rawid（递增_timestamp + 随机_rawid）
                    rawid = raw.get("rawid", "")
                    timestamp = raw.get("timestamp", 0)
                    _time = datetime.fromtimestamp(
                        timestamp) if timestamp else datetime.now()

                    # 超过5 min的消息忽略
                    if time.time() - timestamp > 60 * 5:
                        logger.debug(
                            f"Ignored old message: {raw.get('content', '')[:30]}...")
                        continue

                    adapted = {
                        "local_id": f"{timestamp}_{rawid}" if rawid else timestamp,
                        "chat": raw.get("sessionId", ""),
                        "chat_name": raw.get("groupName", raw.get("sourceName", "")),
                        "sender": raw.get("sourceName", ""),
                        "content": raw.get("content", ""),
                        "time": _time.strftime("%m-%d %H:%M"),
                        "raw": raw,
                    }

                    self.signals.put("wechat_message", adapted)
                    logger.info(
                        f"SENT [{adapted['chat_name']}] {adapted['sender']}: {adapted['content'][:30]}...")

            except requests.exceptions.RequestException as e:
                logger.error(f"WeFlowSource connection error: {e}")
                if not self.signals.terminate:
                    time.sleep(5)  # 重试前等待
            except Exception as e:
                logger.error(f"WeFlowSource error: {e}")
                if not self.signals.terminate:
                    time.sleep(5)

        logger.info("WeFlowSource stopped")
