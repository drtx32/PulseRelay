"""
OpenClaw Gateway - 中转站，接收数据源消息，触发后发送到 OpenClaw

Usage:
    1. Copy .env.example to .env and fill in your values
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

import websockets
import jinja2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from base import Signals
from config import load_config
from core.trigger_engine import TriggerEngine, TriggerConfig, Message
from sources import LarkWebhookSource, WeFlowSource
from handlers.bark import bark_notify, init_bark_handler

# Load .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Configuration
_CONFIG = load_config()
_OPENCLAW_CONFIG = _CONFIG["openclaw"]
_BARK_CONFIG = _CONFIG["handlers"]["bark"]
_AGGREGATION_CONFIG = _CONFIG["aggregation"]
_SOURCES_CONFIG = _CONFIG["sources"]

WS_HOST = _OPENCLAW_CONFIG["ws_host"]
WS_PORT = int(_OPENCLAW_CONFIG["ws_port"])
WS_PATH = _OPENCLAW_CONFIG["ws_path"]
SENDER_ID = _OPENCLAW_CONFIG["sender_id"]
SENDER_NAME = _OPENCLAW_CONFIG["sender_name"]
WS_TOKEN = _OPENCLAW_CONFIG.get("ws_token", "")
BARK_DEVICE_KEY = _BARK_CONFIG.get("device_key", "")

# Build WebSocket URL with optional token
WS_URL = f"ws://{WS_HOST}:{WS_PORT}{WS_PATH}?senderId={SENDER_ID}&senderName={SENDER_NAME}"
if WS_TOKEN:
    WS_URL += f"&token={WS_TOKEN}"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=_CONFIG["server"].get("title", "PulseRelay Gateway"))

# 挂载静态文件
assets_path = Path(__file__).parent / "assets"
if assets_path.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

# 当前连接的浏览器 WebSocket
current_browser_ws: WebSocket | None = None

# TriggerEngine 配置：这些聚合规则对所有消息源适用。
_trigger_config = TriggerConfig(
    content_threshold=int(_AGGREGATION_CONFIG.get("content_threshold", 1000)),
    message_threshold=int(_AGGREGATION_CONFIG.get("message_threshold", 10)),
    idle_timeout=float(_AGGREGATION_CONFIG.get("idle_timeout", 20)),
    min_trigger_interval=float(_AGGREGATION_CONFIG.get("min_trigger_interval", 5.0)),
)

# 全局状态
_signals: Signals = None
_trigger_engine: TriggerEngine = None
_sources: dict = {}
_template: str = None


def format_messages(messages: list[Message], template: str = None) -> str:
    """Format messages using template"""
    if not messages:
        return ""

    # 如果没有模板，使用默认格式
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
    """异步处理触发（发送到浏览器和 OpenClaw）"""
    global current_browser_ws

    # 先把摘要发送到浏览器显示
    if current_browser_ws:
        try:
            aggregate_msg = {
                "type": "relay.aggregate.message",
                "content": content,
                "senderId": SENDER_ID,
                "senderName": SENDER_NAME
            }
            await current_browser_ws.send_text(json.dumps(aggregate_msg))
            logger.info("Sent aggregate message to browser")
        except Exception as e:
            logger.error(f"Failed to send to browser: {e}")

    # 发送到 OpenClaw
    await _forward_to_openclaw_async(content, senderId=SENDER_ID, senderName=SENDER_NAME)


async def _forward_to_openclaw_async(content: str, senderId: str, senderName: str):
    """异步发送到 OpenClaw 并把响应发回浏览器"""
    global current_browser_ws
    full_content = ""
    bark_sent = False
    try:
        async with websockets.connect(WS_URL) as openclaw_ws:
            msg = {
                "type": "chat.send",
                "messageId": f"msg_relay_{int(time.time() * 1000)}",
                "content": content,
                "senderId": senderId,
                "senderName": senderName
            }
            await openclaw_ws.send(json.dumps(msg))
            logger.info(f"Sent to OpenClaw: {content[:100]}")

            async for message in openclaw_ws:
                msg_data = json.loads(message)

                if current_browser_ws:
                    msg_data["fromPulseRelay"] = True
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
    """主循环：消费 Signals.queue + 定期检查触发条件"""
    global _signals, _trigger_engine

    while True:
        try:
            # 从 queue 获取消息（非阻塞）
            item = _trigger_engine.consume_signals(timeout=0.1)
            if item:
                key, raw = item
                if key.endswith("_message"):
                    _trigger_engine.process_raw(raw)

            # 检查触发条件
            result = _trigger_engine.check_trigger()
            if result.triggered:
                _handle_trigger(result)

            # 小延迟避免 busy loop
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"Main loop error: {e}")


def _handle_trigger(result):
    """处理触发"""
    global _template
    content = format_messages(result.messages, _template)
    logger.info(f"\n=== TRIGGER: {result.reason} ===\n{content}\n===")

    # 调度异步任务处理
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_handle_trigger_async(content))
        loop.close()
    except Exception as e:
        logger.error(f"Failed to schedule trigger: {e}")

    # 重置 engine
    _trigger_engine.reset()


@app.on_event("startup")
def startup():
    """启动时初始化 Signals、TriggerEngine、数据源"""
    global _signals, _trigger_engine, _sources, _template

    # 加载模板
    template_path_str = _CONFIG.get("templates", {}).get("message", "templates/message_summary_example.j2")
    template_path = Path(__file__).parent / template_path_str
    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            _template = f.read()
        logger.info(f"Loaded template from {template_path}")
    else:
        _template = None
        logger.info("No template file found, using default format")

    # 初始化 Signals
    _signals = Signals()
    _sources = {}

    # 初始化 Bark handler
    init_bark_handler(BARK_DEVICE_KEY)
    logger.info(f"Bark enabled: {bool(BARK_DEVICE_KEY)}")

    # 读取监控会话列表（对所有消息源适用）
    monitor_chats = _AGGREGATION_CONFIG.get("monitor_chats", [])
    if isinstance(monitor_chats, str):
        monitor_chats = [c.strip() for c in monitor_chats.split(",") if c.strip()]

    # 初始化 TriggerEngine
    _trigger_engine = TriggerEngine(
        signals=_signals,
        config=_trigger_config,
        monitor_chats=monitor_chats
    )

    # 初始化数据源
    weflow_config = _SOURCES_CONFIG.get("weflow", {})
    if weflow_config.get("enabled", True):
        _sources["weflow"] = WeFlowSource(
            signals=_signals,
            host=weflow_config.get("host", "localhost"),
            port=int(weflow_config.get("port", 5031)),
            access_token=weflow_config.get("access_token", ""),
            enabled=True
        )
        _signals.register_source("weflow", _sources["weflow"])

    lark_config = _SOURCES_CONFIG.get("lark", {})
    if lark_config.get("enabled", False):
        _sources["lark"] = LarkWebhookSource(
            signals=_signals,
            verification_token=lark_config.get("verification_token", ""),
            encrypt_key=lark_config.get("encrypt_key", ""),
            include_non_text=bool(lark_config.get("include_non_text", True)),
            enabled=True,
        )
        _signals.register_source("lark", _sources["lark"])

    # 启动数据源线程
    for name, src in _sources.items():
        if src.enabled:
            t = threading.Thread(target=src.init_event_loop, daemon=True)
            t.start()
            logger.info(f"Started source: {name}")

    # 启动主循环线程
    _main_thread = threading.Thread(target=_main_loop, daemon=True)
    _main_thread.start()
    logger.info("Main loop started")


@app.on_event("shutdown")
def shutdown():
    """关闭时停止所有数据源"""
    global _signals, _sources
    if _signals:
        _signals.terminate = True
    for name, src in _sources.items():
        logger.info(f"Stopping source: {name}")
    logger.info("Shutdown complete")


@app.post(_SOURCES_CONFIG.get("lark", {}).get("callback_path", "/sources/lark/events"))
async def lark_events(raw: dict):
    """飞书/Lark 事件订阅回调入口。"""
    source = _sources.get("lark")
    if not source or not source.enabled:
        raise HTTPException(status_code=404, detail="lark source disabled")

    result = source.handle_event(raw)
    if result.get("error") == "invalid_lark_verification_token":
        raise HTTPException(status_code=401, detail=result["error"])
    if result.get("error") == "encrypted_lark_callback_not_supported":
        raise HTTPException(status_code=501, detail=result["error"])
    return result


@app.post("/queue-message")
async def queue_message(raw: dict):
    """兼容旧接口：直接接收消息放入 queue（不推荐，新数据源应继承 Module）"""
    if _signals:
        _signals.put('manual_message', raw)
        return {"status": "queued"}
    return {"status": "signals_not_ready"}

class SendMessageRequest(BaseModel):
    content: str
    senderId: str = "pulserelay"
    senderName: str = "PulseRelay"


@app.get("/", response_class=HTMLResponse)
async def get_html():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PulseRelay Gateway</title>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <style>
            body { font-family: Arial, sans-serif; max-width: 900px; margin: 50px auto; padding: 20px; }
            .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
            .connected { background: #d4edda; color: #155724; }
            .disconnected { background: #f8d7da; color: #721c24; }
            #messages { border: 1px solid #ddd; height: 500px; overflow-y: auto; padding: 15px; margin: 10px 0; background: #fafafa; }
            textarea { width: 100%; padding: 10px; box-sizing: border-box; resize: vertical; }
            .btn { padding: 10px 20px; cursor: pointer; }
            .user { color: #0066cc; margin: 10px 0; }
            .user::before { content: "👤 You: "; font-weight: bold; }
            .agent { color: #333; margin: 10px 0; padding: 10px; background: #fff; border-radius: 5px; border: 1px solid #eee; }
            .agent::before { content: "🤖 Agent: "; font-weight: bold; color: #00cc66; }
            .typing { color: #999; font-style: italic; }
            .error { color: #cc0000; }
            .system { color: #666; font-style: italic; }
            .aggregate { color: #9933ff; background: #f3e6ff; padding: 10px; border-radius: 5px; margin: 10px 0; }
            .aggregate::before { content: "📨 Aggregate Summary: "; font-weight: bold; color: #9933ff; }
            pre { background: #f4f4f4; padding: 10px; border-radius: 5px; overflow-x: auto; }
            code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
            pre code { background: none; padding: 0; }
            h1, h2, h3 { margin-top: 1em; }
            ul, ol { padding-left: 20px; }
            blockquote { border-left: 3px solid #ccc; margin-left: 0; padding-left: 15px; color: #666; }
            #stats { background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 10px 0; font-size: 14px; }
            #stats h3 { margin-top: 0; color: #333; }
            .stat-row { display: flex; gap: 20px; flex-wrap: wrap; }
            .stat-item { min-width: 120px; }
            .stat-label { color: #666; font-size: 12px; }
            .stat-value { font-weight: bold; font-size: 18px; color: #333; }
            .progress-bar { background: #ddd; height: 8px; border-radius: 4px; margin-top: 4px; }
            .progress-fill { background: #00cc66; height: 100%; border-radius: 4px; transition: width 0.3s; }
        </style>
    </head>
    <body>
        <h1>PulseRelay Gateway</h1>
        <div id="status" class="status disconnected">Disconnected</div>

        <div id="stats">
            <h3>📊 Trigger Engine 状态</h3>
            <div class="stat-row">
                <div class="stat-item">
                    <div class="stat-label">消息数</div>
                    <div class="stat-value"><span id="msg-count">0</span> / <span id="msg-threshold">10</span></div>
                    <div class="progress-bar"><div class="progress-fill" id="msg-progress" style="width: 0%"></div></div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">字符数</div>
                    <div class="stat-value"><span id="char-count">0</span> / <span id="char-threshold">1000</span></div>
                    <div class="progress-bar"><div class="progress-fill" id="char-progress" style="width: 0%"></div></div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">空闲时间</div>
                    <div class="stat-value"><span id="idle-time">0</span>s / <span id="idle-timeout">20</span>s</div>
                    <div class="progress-bar"><div class="progress-fill" id="idle-progress" style="width: 0%"></div></div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">距离触发</div>
                    <div class="stat-value" id="next-push">--</div>
                </div>
            </div>
            <div style="margin-top: 10px; font-size: 12px; color: #666;">
                监控群聊: <span id="monitor-chats">--</span>
            </div>
        </div>

        <div id="messages"></div>
        <textarea id="input" rows="3" placeholder="Type your message... (Enter for new line, Ctrl+Enter to send)" onkeydown="handleKeyDown(event)"></textarea>
        <button class="btn" onclick="sendMessage()">Send</button>
        <button class="btn" onclick="clearMessages()">Clear</button>

        <script>
            let ws = null;
            let messageId = 0;
            let streamContent = '';
            let isStreaming = false;

            function updateStats() {
                fetch('/stats')
                    .then(r => r.json())
                    .then(data => {
                        if (data.status === 'running') {
                            document.getElementById('msg-count').textContent = data.messages;
                            document.getElementById('msg-threshold').textContent = data.config.message_threshold;
                            document.getElementById('msg-progress').style.width = Math.min(100, data.messages / data.config.message_threshold * 100) + '%';

                            document.getElementById('char-count').textContent = data.total_chars;
                            document.getElementById('char-threshold').textContent = data.config.content_threshold;
                            document.getElementById('char-progress').style.width = Math.min(100, data.total_chars / data.config.content_threshold * 100) + '%';

                            if (data.waiting_for_message) {
                                document.getElementById('idle-time').textContent = '等待消息...';
                                document.getElementById('idle-timeout').textContent = '';
                                document.getElementById('idle-progress').style.width = '0%';
                                document.getElementById('next-push').textContent = '--';
                            } else {
                                document.getElementById('idle-time').textContent = Math.round(data.idle_seconds);
                                document.getElementById('idle-timeout').textContent = data.config.idle_timeout;
                                document.getElementById('idle-progress').style.width = Math.min(100, data.idle_seconds / data.config.idle_timeout * 100) + '%';
                                const nextPush = Math.max(0, Math.round(data.next_push_in));
                                document.getElementById('next-push').textContent = nextPush + 's';
                            }

                            document.getElementById('monitor-chats').textContent = data.monitor_chats.join(', ');
                        }
                    })
                    .catch(() => {});
            }

            setInterval(updateStats, 1000);

            function connect() {
                ws = new WebSocket('ws://' + window.location.host + '/ws');

                ws.onopen = () => {
                    document.getElementById('status').textContent = 'Connected to PulseRelay';
                    document.getElementById('status').className = 'status connected';
                    addMessage('system', 'Connected');
                };

                ws.onclose = () => {
                    document.getElementById('status').textContent = 'Disconnected';
                    document.getElementById('status').className = 'status disconnected';
                    addMessage('system', 'Disconnected');
                };

                ws.onerror = (err) => {
                    addMessage('error', 'WebSocket error');
                };

                ws.onmessage = (event) => {
                    const msg = JSON.parse(event.data);
                    handleMessage(msg);
                };
            }

            function handleMessage(msg) {
                switch(msg.type) {
                    case 'chat.typing':
                        if (!isStreaming) {
                            isStreaming = true;
                            addMessage('typing', 'Agent is typing...');
                        }
                        break;
                    case 'chat.stream':
                        streamContent = msg.content || '';
                        updateStreamContent(msg);
                        break;
                    case 'chat.response':
                        // chat.response 的 content 可能包含完整内容，不累加直接 finalize
                        finalizeStream();
                        break;
                    case 'chat.error':
                        finalizeStream();
                        addMessage('error', 'Error: ' + (msg.error || 'Unknown error'));
                        break;
                    case 'relay.aggregate.message':
                        addAggregateMessage(msg.content);
                        break;
                }
            }

            function updateStreamContent(msg) {
                const messages = document.getElementById('messages');
                let last = messages.lastElementChild;
                const className = msg.fromPulseRelay ? 'aggregate' : 'agent';
                if (!last || (last.className !== 'agent' && last.className !== 'aggregate')) {
                    last = document.createElement('div');
                    last.className = className;
                    messages.appendChild(last);
                } else if (last.className !== className) {
                    last = document.createElement('div');
                    last.className = className;
                    messages.appendChild(last);
                }
                last.innerHTML = marked.parse(streamContent);
                messages.scrollTop = messages.scrollHeight;
            }

            function finalizeStream() {
                streamContent = '';
                isStreaming = false;
            }

            function sendMessage() {
                const input = document.getElementById('input');
                const content = input.value.trim();
                if (!content || !ws || ws.readyState !== WebSocket.OPEN) return;

                const msg = {
                    type: 'chat.send',
                    messageId: 'msg_' + (++messageId),
                    content: content
                };

                ws.send(JSON.stringify(msg));
                addMessage('user', content);
                input.value = '';
            }

            function handleKeyDown(event) {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault();
                    sendMessage();
                }
            }

            function addMessage(type, content) {
                const div = document.createElement('div');
                div.className = type;
                div.innerHTML = marked.parse(content);
                document.getElementById('messages').appendChild(div);
                document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
            }

            function addAggregateMessage(content) {
                const messages = document.getElementById('messages');
                const div = document.createElement('div');
                div.className = 'aggregate';
                div.innerHTML = marked.parse(content);
                messages.appendChild(div);
                messages.scrollTop = messages.scrollHeight;
            }

            function clearMessages() {
                document.getElementById('messages').innerHTML = '';
                streamContent = '';
                isStreaming = false;
            }

            connect();
        </script>
    </body>
    </html>
    """


@app.websocket("/ws")
async def websocket_proxy(websocket: WebSocket):
    """Proxy WebSocket - connects to OpenClaw and forwards messages"""
    global current_browser_ws
    await websocket.accept()
    current_browser_ws = websocket
    logger.info("Browser connected to proxy")

    openclaw_ws = None
    try:
        async with websockets.connect(WS_URL) as openclaw_ws:
            logger.info(f"Connected to OpenClaw: {WS_URL}")

            async def forward_to_openclaw():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await openclaw_ws.send(data)
                        logger.info(f"Forwarded to OpenClaw: {data[:100]}")
                except WebSocketDisconnect:
                    global current_browser_ws
                    if current_browser_ws == websocket:
                        current_browser_ws = None
                    logger.info("Browser disconnected")
                except Exception as e:
                    logger.error(f"Forward error: {e}")

            async def forward_to_browser():
                full_content = ""
                bark_sent_for_current = False
                try:
                    async for message in openclaw_ws:
                        msg = json.loads(message)
                        await websocket.send_text(message)
                        logger.info(f"Forwarded to browser: {msg.get('type')}")

                        if msg.get("type") == "chat.stream":
                            full_content = msg.get("content", "") or ""
                            bark_sent_for_current = False

                        if msg.get("type") == "chat.response" and msg.get("done"):
                            if not bark_sent_for_current:
                                bark_sent_for_current = True
                                if full_content:
                                    final_text = full_content
                                else:
                                    final_text = msg.get("content", "") or ""
                                logger.info(f"chat.response done: final_text='{final_text[:100]}'")
                                if final_text:
                                    bark_notify("OpenClaw Agent", final_text[:500])
                        elif msg.get("type") == "chat.error":
                            if not bark_sent_for_current:
                                bark_sent_for_current = True
                                bark_notify("OpenClaw Error", msg.get("error", "Unknown error"))
                except websockets.exceptions.ConnectionClosed:
                    logger.info("OpenClaw connection closed")
                except Exception as e:
                    logger.error(f"Browser forward error: {e}")

            await asyncio.gather(
                forward_to_openclaw(),
                forward_to_browser()
            )

    except Exception as e:
        logger.error(f"OpenClaw connection failed: {e}")
        bark_notify("OpenClaw Error", f"Connection failed: {e}")


@app.post("/send-message")
async def send_message(req: SendMessageRequest):
    """Receive message and send to OpenClaw via WebSocket"""
    logger.info(f"Received message: {req.content[:100]}")

    asyncio.create_task(_forward_to_openclaw_async(req.content, req.senderId, req.senderName))
    return {"status": "forwarded"}


@app.get("/notify")
async def test_bark(title: str = "Test", content: str = "Bark is working!"):
    """Test Bark notification"""
    success = bark_notify(title, content)
    return {"success": success, "device_key": BARK_DEVICE_KEY[:8] + "..." if BARK_DEVICE_KEY else "not set"}


@app.get("/ws-url")
async def get_ws_url():
    """Get the configured WebSocket URL"""
    return {"url": WS_URL}


@app.get("/stats")
async def get_stats():
    """Get trigger engine stats"""
    if _trigger_engine:
        stats = _trigger_engine.get_stats()
        config = _trigger_engine.config
        monitor_chats = _trigger_engine.monitor_chats or ["所有会话"]
        return {
            "status": "running",
            "messages": stats["message_count"],
            "total_chars": stats["total_chars"],
            "idle_seconds": round(stats["idle_seconds"], 1),
            "seen_keys": stats["seen_keys"],
            "waiting_for_message": stats["waiting_for_message"],
            "config": {
                "content_threshold": config.content_threshold,
                "message_threshold": config.message_threshold,
                "idle_timeout": config.idle_timeout,
                "min_trigger_interval": config.min_trigger_interval,
            },
            "monitor_chats": monitor_chats,
            "next_push_in": max(0, config.min_trigger_interval - (time.monotonic() - _trigger_engine.last_trigger_time)),
        }
    return {"status": "not_running"}


if __name__ == "__main__":
    import uvicorn
    print(f"OpenClaw WebSocket URL: {WS_URL}")
    print(f"Bark enabled: {bool(BARK_DEVICE_KEY)}")
    print(f"Open http://localhost:8000 in your browser to test")
    uvicorn.run(app, host=_CONFIG["server"].get("host", "0.0.0.0"), port=int(_CONFIG["server"].get("port", 8000)), access_log=False)
