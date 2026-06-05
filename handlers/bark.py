"""
Bark 通知处理器
"""

import logging
import requests

logger = logging.getLogger(__name__)


class BarkHandler:
    def __init__(self, device_key: str = None):
        self.device_key = device_key
        self.api_url = "https://api.day.app/push"

    def notify(
        self,
        title: str,
        content: str,
        group: str = "PulseRelay",
        icon: str = "https://s41.ax1x.com/2026/05/19/pexveh9.png"
    ) -> bool:
        """Send notification via Bark"""
        if not self.device_key:
            logger.warning("BARK_DEVICE_KEY not configured, skipping notification")
            return False

        try:
            payload = {
                "device_key": self.device_key,
                "title": title,
                "body": content,
                "group": group,
                "icon": icon,
            }
            resp = requests.post(self.api_url, json=payload, timeout=10)
            result = resp.json()
            if result.get("code", -1) == 200:
                logger.info(f"Bark notification sent: {title}")
                return True
            else:
                logger.error(f"Bark notification failed: {result}")
                return False
        except Exception as e:
            logger.error(f"Bark notification error: {e}")
            return False


# 全局单例
_bark_handler: BarkHandler = None


def init_bark_handler(device_key: str):
    global _bark_handler
    _bark_handler = BarkHandler(device_key)


def bark_notify(title: str, content: str, group: str = "PulseRelay") -> bool:
    """Send Bark notification"""
    if _bark_handler:
        return _bark_handler.notify(title, content, group)
    return False
