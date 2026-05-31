"""
OpenClaw Gateway - 中转站，接收数据源消息，触发后发送到 OpenClaw

Usage:
    1. Configure runtime settings in data/config.yaml
    2. Run: uvicorn gateway:app --reload --port 8000
    3. Open http://localhost:8000 in browser
"""

import os
import json
import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

import websockets
import jinja2
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from base import Signals
from core.config import ConfigLoader
from core.trigger_engine import TriggerEngine, TriggerConfig, Message
from sources import GitHubWebhookSource, SlackSource, TelegramSource, WeFlowSource
from handlers.bark import bark_notify, init_bark_handler

# Load .env file for deployment/runtime overrides only
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PulseRelay Gateway")

# 挂载静态文件
assets_path = Path(__file__).parent / "assets"
if assets_path.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

# Runtime config
_config_loader = ConfigLoader()
_runtime_config = _config_loader.load()

# OpenClaw / WebSocket runtime config
WS_HOST = os.getenv("WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "18800"))
WS_PATH = os.getenv("WS_PATH", "/ws")
SENDER_ID = os.getenv("SENDER_ID", "test_user_001")
SENDER_NAME = os.getenv("SENDER_NAME", "TestUser")
WS_TOKEN = os.getenv("WS_TOKEN", "")

# Build WebSocket URL with optional token
WS_URL = f"ws://{WS_HOST}:{WS_PORT}{WS_PATH}?senderId={SENDER_ID}&senderName={SENDER_NAME}"
if WS_TOKEN:
    WS_URL += f"&token={WS_TOKEN}"

# 当前连接的浏览器 WebSocket
current_browser_ws: WebSocket | None = None

# TriggerEngine 配置
_trigger_config = TriggerConfig(
    content_threshold=int(os.getenv("WX_CONTENT_THRESHOLD", "1000")),
    message_threshold=int(os.getenv("WX_MESSAGE_THRESHOLD", "10")),
    idle_timeout=float(os.getenv("WX_IDLE_TIMEOUT", "20")),
    min_trigger_interval=5.0
)

# 全局状态
_signals: Signals = None
_trigger_engine: TriggerEngine = None
_sources: dict[str, Any] = {}
_source_threads: dict[str, threading.Thread] = {}
_template: str = None
_github_webhook_path: str = "/webhooks/github"


def format_messages(messages: list[Message], template: str = None) -> str:
    """Format messages using template"""
    if not messages:
        return ""

    if not template:
        return "\n".join([
            f"**{msg.sender}**: {msg.content}"
            for msg in messages
        ])

    try:
        env = jinja2.Environment()
        t = env.from_string(template)
        return t.render(messages=messages)
    except Exception as e:
        logger.error(f"Template render error: {e}")
        return "\n".join([
            f"**{msg.sender}**: {msg.content}"
            for msg in messages
        ])


async def _handle_trigger_async(content: str):
    global current_browser_ws

    if current_browser_ws:
        try:
            wx_msg = {
                "type": "wx.monitor.message",
                "content": content,
                "senderId": SENDER_ID,
                "senderName": SENDER_NAME
            }
            await current_browser_ws.send_text(json.dumps(wx_msg))
            logger.info("Sent wx_monitor message to browser")
        except Exception as e:
            logger.error(f"Failed to send to browser: {e}")

    await _forward_to_openclaw_async(content, senderId=SENDER_ID, senderName=SENDER_NAME)


async def _forward_to_openclaw_async(content: str, senderId: str, senderName: str):
    global current_browser_ws
    full_content = ""
    bark_sent = False

    try:
        async with websockets.connect(WS_URL) as openclaw_ws:
            msg = {
                "type": "chat.send",
                "messageId": f"msg_wx_{int(time.time() * 1000)}",
                "content": content,
                "senderId": senderId,
                "senderName": senderName
            }
            await openclaw_ws.send(json.dumps(msg))
            logger.info(f"Sent to OpenClaw: {content[:100]}")

            async for message in openclaw_ws:
                msg_data = json.loads(message)

                if current_browser_ws:
                    msg_data["fromWxMonitor"] = True
                    await current_browser_ws.send_text(json.dumps(msg_data))

                if msg_data.get("type") == "chat.stream":
                    full_content = msg_data.get("content", "") or ""

                if msg_data.get("type") == "chat.response" and msg_data.get("done"):
                    if not bark_sent:
                        bark_sent = True
                        final_text = full_content if full_content else msg_data.get("content", "") or ""
                        if final_text:
                            bark_notify("OpenClaw Agent", final_text[:500])
                    break
                elif msg_data.get("type") == "chat.error":
                    if not bark_sent:
                        bark_sent = True
                        bark_notify("OpenClaw Error", msg_data.get("error", "Unknown error"))
                    break

    except Exception as e:
        logger.error(f"Failed to forward to OpenClaw: {e}")


def _main_loop():
    global _signals, _trigger_engine

    while True:
        try:
            try:
                item = _trigger_engine.consume_signals(timeout=0.1)
                if item:
                    _, event = item
                    _trigger_engine.process_event(event)
            except Exception:
                pass

            result = _trigger_engine.check_trigger()
            if result.triggered:
                _handle_trigger(result)

            time.sleep(0.5)

        except Exception as e:
            logger.error(f"Main loop error: {e}")


def _handle_trigger(result):
    global _template
    content = format_messages(result.messages, _template)
    logger.info(f"\n=== TRIGGER: {result.reason} ===\n{content}\n===")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_handle_trigger_async(content))
        loop.close()
    except Exception as e:
        logger.error(f"Failed to schedule trigger: {e}")

    _trigger_engine.reset()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _load_template_from_config(runtime_config: dict[str, Any]) -> str | None:
    weflow_config = runtime_config.get("sources", {}).get("weflow", {})
    template_path_str = (
        weflow_config.get("template", {}).get("path")
        or "templates/wx_template_example.j2"
    )
    template_path = Path(__file__).parent / template_path_str
    if not template_path.exists():
        logger.info("No template file found, using default format")
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
    logger.info(f"Loaded template from {template_path}")
    return template


def _build_sources(signals: Signals, runtime_config: dict[str, Any]) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    source_cfg = runtime_config.get("sources", {})

    weflow_config = source_cfg.get("weflow", {})
    weflow_connection = weflow_config.get("connection", {})
    sources["weflow"] = WeFlowSource(
        event_bus=signals.event_bus,
        host=weflow_connection.get("host", "localhost"),
        port=weflow_connection.get("port", 5031),
        access_token=weflow_connection.get("access_token", ""),
        enabled=_as_bool(weflow_config.get("enabled", True), default=True),
    )

    telegram_config = source_cfg.get("telegram", {})
    telegram_enabled = _as_bool(telegram_config.get("enabled", False), default=False)
    if telegram_enabled:
        if TelegramSource is None:
            logger.warning("Telegram source enabled but python-telegram-bot is not installed")
        else:
            telegram_connection = telegram_config.get("connection", {})
            bot_token = telegram_connection.get("bot_token", "")
            if not bot_token:
                logger.warning("Telegram source enabled but bot_token is missing")
            else:
                telegram_monitor = telegram_config.get("monitor", {})
                sources["telegram"] = TelegramSource(
                    event_bus=signals.event_bus,
                    bot_token=bot_token,
                    allowed_chat_ids=telegram_monitor.get("allowed_chat_ids", []),
                    enabled=True,
                )

    slack_config = source_cfg.get("slack", {})
    slack_enabled = _as_bool(slack_config.get("enabled", False), default=False)
    if slack_enabled:
        if SlackSource is None:
            logger.warning("Slack source enabled but slack-sdk is not installed")
        else:
            slack_connection = slack_config.get("connection", {})
            app_token = slack_connection.get("app_token", "")
            bot_token = slack_connection.get("bot_token", "")
            if not app_token or not bot_token:
                logger.warning("Slack source enabled but app_token/bot_token is missing")
            else:
                slack_monitor = slack_config.get("monitor", {})
                sources["slack"] = SlackSource(
                    event_bus=signals.event_bus,
                    app_token=app_token,
                    bot_token=bot_token,
                    allowed_channels=slack_monitor.get("allowed_channels", []),
                    enabled=True,
                )

    github_config = source_cfg.get("github_webhook", {})
    github_enabled = _as_bool(github_config.get("enabled", False), default=False)
    if github_enabled:
        webhook_cfg = github_config.get("webhook", {})
        sources["github_webhook"] = GitHubWebhookSource(
            event_bus=signals.event_bus,
            webhook_secret=webhook_cfg.get("secret", ""),
            enabled=True,
        )

    return sources


def _start_sources(sources: dict[str, Any]) -> dict[str, threading.Thread]:
    threads: dict[str, threading.Thread] = {}
    for name, src in sources.items():
        if not getattr(src, "enabled", False):
            continue
        t = threading.Thread(
            target=lambda s=src: asyncio.run(s.start()),
            daemon=True,
        )
        t.start()
        threads[name] = t
        logger.info(f"Started source adapter: {name}")
    return threads


def _get_github_webhook_path(runtime_config: dict[str, Any]) -> str:
    source_cfg = runtime_config.get("sources", {})
    github_cfg = source_cfg.get("github_webhook", {})
    webhook_cfg = github_cfg.get("webhook", {})
    path = webhook_cfg.get("path", "/webhooks/github")
    if not isinstance(path, str) or not path.startswith("/"):
        return "/webhooks/github"
    return path


async def _handle_github_webhook_request(request: Request):
    source = _sources.get("github_webhook")
    if source is None or not isinstance(source, GitHubWebhookSource):
        raise HTTPException(status_code=404, detail="GitHub webhook source not enabled")

    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc.msg}") from exc

    event = await source.on_webhook(
        payload=payload,
        headers=dict(request.headers),
        raw_body=raw_body,
    )
    if event is None:
        return {"ok": True, "processed": False}

    return {
        "ok": True,
        "processed": True,
        "event_type": event.event.type,
        "dedupe_key": event.event.dedupe_key,
    }


@app.post("/webhooks/github")
async def github_webhook_default(request: Request):
    return await _handle_github_webhook_request(request)


@app.on_event("startup")
def startup():
    """启动时初始化 Signals、TriggerEngine、SourceAdapter"""
    global _signals, _trigger_engine, _sources, _source_threads, _template, _github_webhook_path

    _template = _load_template_from_config(_runtime_config)

    _signals = Signals()

    bark_config = _runtime_config.get("deliveries", {}).get("bark", {})
    bark_device_key = bark_config.get("device_key", "")

    init_bark_handler(bark_device_key)
    logger.info(f"Bark enabled: {bool(bark_device_key)}")

    weflow_config = _runtime_config.get("sources", {}).get("weflow", {})
    monitor_chats = (
        weflow_config.get("monitor", {}).get("chats", [])
    )

    trigger_cfg = weflow_config.get("trigger", {})

    _trigger_engine = TriggerEngine(
        signals=_signals,
        config=TriggerConfig(
            content_threshold=trigger_cfg.get("content_threshold", 1000),
            message_threshold=trigger_cfg.get("message_threshold", 10),
            idle_timeout=trigger_cfg.get("idle_timeout", 20),
            min_trigger_interval=trigger_cfg.get("min_trigger_interval", 5),
        ),
        monitor_chats=monitor_chats
    )

    _sources = _build_sources(_signals, _runtime_config)
    for source_name, source in _sources.items():
        _signals.register_source(source_name, source)

    _source_threads = _start_sources(_sources)

    _github_webhook_path = _get_github_webhook_path(_runtime_config)
    if _github_webhook_path != "/webhooks/github":
        known_paths = {getattr(route, "path", "") for route in app.router.routes}
        if _github_webhook_path not in known_paths:
            app.add_api_route(
                _github_webhook_path,
                _handle_github_webhook_request,
                methods=["POST"],
                name="github_webhook_configured",
            )
            logger.info(f"Registered GitHub webhook path: {_github_webhook_path}")

    _main_thread = threading.Thread(target=_main_loop, daemon=True)
    _main_thread.start()
    logger.info("Main loop started")


@app.on_event("shutdown")
async def shutdown():
    global _signals, _sources

    if _signals:
        _signals.terminate = True

    for name, src in _sources.items():
        try:
            await src.stop()
        except Exception as exc:
            logger.warning(f"Failed to stop source {name}: {exc}")
        logger.info(f"Stopping source: {name}")

    logger.info("Shutdown complete")
