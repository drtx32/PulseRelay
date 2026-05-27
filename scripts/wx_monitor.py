"""
WeChat Monitor - 读取 wx monitor --json 的 subprocess，发送消息到 gateway

Usage:
    1. Copy .env.example to .env and fill in your values
    2. Run: python wx_monitor.py

Messages are sent to gateway via HTTP POST.
"""

import os
import json
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Configuration
WX_COMMAND = os.getenv("WX_COMMAND", "wx monitor --json")
WX_MESSAGE_OFFSET = int(os.getenv("WX_MESSAGE_OFFSET", "0"))
WX_ENDPOINT = os.getenv("WX_ENDPOINT", "http://127.0.0.1:8000/queue-message")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WeChatMonitor:
    """读取 wx subprocess，发送消息到中转站"""

    def __init__(self):
        self.running = False

    def start(self):
        """Start monitoring wx command"""
        import subprocess

        self.running = True

        logger.info(f"Starting: {WX_COMMAND}")
        logger.info(f"Endpoint: {WX_ENDPOINT}")

        process = subprocess.Popen(
            WX_COMMAND.split(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        skip_lines = WX_MESSAGE_OFFSET

        try:
            while self.running:
                line_bytes = process.stdout.readline()
                if not line_bytes:
                    break

                # Decode
                try:
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    try:
                        line = line_bytes.decode('gbk').strip()
                    except:
                        continue

                if not line:
                    continue

                # Skip status lines
                if skip_lines > 0:
                    skip_lines -= 1
                    logger.debug(f"skipped: {line[:100]}")
                    continue

                # Parse JSON
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 发送到中转站
                try:
                    resp = requests.post(WX_ENDPOINT, json=raw, timeout=5)
                    if resp.status_code == 200:
                        chat = raw.get('chat', '?')
                        sender = raw.get('sender_name', raw.get('sender', '?')) or '未知'
                        content = raw.get('content', '')[:50] if raw.get('content') else f"[{raw.get('msg_type', '未知')}]"
                        logger.info(f"SENT [{chat}] {sender}: {content}...")
                    else:
                        logger.error(f"Failed to send: {resp.status_code}")
                except Exception as e:
                    logger.error(f"Failed to send to endpoint: {e}")

        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.running = False
            process.terminate()
            process.wait()
            logger.info("Monitor stopped")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="WeChat Monitor")
    args = parser.parse_args()

    monitor = WeChatMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
