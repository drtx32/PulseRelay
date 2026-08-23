"""Forward newly received SmsForwarder events to a WeCom bot webhook."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed
from zoneinfo import ZoneInfo


API_URL = os.environ.get("PULSERELAY_API_URL", "http://pulserelay:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("PULSERELAY_ADMIN_TOKEN", "").strip()
EVENT_STREAM_URL = os.environ.get("PULSERELAY_EVENT_STREAM_URL", "").strip()
WEIXIN_URL = os.environ.get("WEIXIN_WORK_WEBHOOK_URL", "").strip()
MANIFEST_WEBHOOKS = os.environ.get("PULSERELAY_WEBHOOKS_JSON", "")
RECONNECT_SECONDS = max(1, int(float(os.environ.get("EVENT_STREAM_RECONNECT_SECONDS", "3"))))
INITIAL_SYNC_SKIP = os.environ.get("SMS_FORWARDER_INITIAL_SYNC_SKIP", "true").lower() == "true"
STATE_DIR = Path(os.environ.get("PULSERELAY_STATE_DIR", ".state"))
STATE_FILE = STATE_DIR / "checkpoint.json"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def format_china_time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "")


def event_stream_url() -> str:
    if EVENT_STREAM_URL:
        return EVENT_STREAM_URL
    if API_URL.startswith("https://"):
        return "wss://" + API_URL[8:] + "/v1/events/stream"
    return "ws://" + API_URL[7:] + "/v1/events/stream"


def configured_webhook_url() -> str:
    """Prefer the manifest webhook list; ignore unresolved ${...} placeholders."""
    try:
        configured = json.loads(MANIFEST_WEBHOOKS)
    except (json.JSONDecodeError, TypeError):
        configured = []
    if isinstance(configured, list):
        for item in configured:
            if isinstance(item, dict) and item.get("enabled", True):
                url = str(item.get("url") or "").strip()
                if url and not url.startswith("${"):
                    return url
    return "" if WEIXIN_URL.startswith("${") else WEIXIN_URL


def load_seen() -> tuple[set[str], bool]:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(item) for item in payload.get("seen", [])}, True
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return set(), False


def save_seen(seen: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"seen": list(seen)[-1000:]}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def is_sms_event(item: object) -> bool:
    return (
        isinstance(item, dict)
        and (item.get("source") or {}).get("type") == "sms_forwarder"
        and (item.get("event") or {}).get("type") == "message.created"
    )


def event_stream():
    if not ADMIN_TOKEN:
        raise RuntimeError("PULSERELAY_ADMIN_TOKEN is required for the event stream")
    with connect(event_stream_url(), open_timeout=30, close_timeout=5, ping_interval=20, ping_timeout=20) as socket:
        socket.send(json.dumps({"token": ADMIN_TOKEN}))
        print("Connected to PulseRelay event stream", flush=True)
        while True:
            raw = socket.recv()
            if raw is None:
                return
            try:
                event = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if is_sms_event(event):
                yield event


def wecom_payload(event: dict) -> dict:
    content = event.get("content") or {}
    source = event.get("source") or {}
    sender = (event.get("sender") or {}).get("name") or (content.get("raw") or {}).get("sender") or "unknown"
    received_at = format_china_time(event.get("received_at") or (event.get("event") or {}).get("timestamp") or "")
    text = str(content.get("text") or "")
    message = f"SmsForwarder 消息\n来源：{sender}\n时间：{received_at}\n\n{text}"
    return {"msgtype": "text", "text": {"content": message}}


def publish(event: dict) -> None:
    webhook_url = configured_webhook_url()
    if not webhook_url:
        raise RuntimeError("an enabled webhook URL is required in connector.yaml")
    body = json.dumps(wecom_payload(event), ensure_ascii=False).encode("utf-8")
    request = Request(webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read(1024 * 1024).decode("utf-8"))
    if response.status < 200 or response.status >= 300 or result.get("errcode") not in (0, None):
        raise RuntimeError(f"WeCom rejected message: HTTP {response.status}, errcode={result.get('errcode')}")


def poll_once() -> int:
    raise RuntimeError("polling is no longer used; consume the PulseRelay event stream")


def main() -> None:
    seen, initialized = load_seen()
    if not initialized and INITIAL_SYNC_SKIP:
        save_seen(seen)
        print("Initialized live event-stream checkpoint; existing events will be skipped", flush=True)
    print(f"Watching PulseRelay event stream: {event_stream_url()}", flush=True)
    while True:
        try:
            for event in event_stream():
                event_id = str(event.get("id") or "")
                if not event_id or event_id in seen:
                    continue
                publish(event)
                seen.add(event_id)
                save_seen(seen)
                print(f"Published SmsForwarder event {event_id}", flush=True)
        except (ConnectionClosed, OSError, TimeoutError, ValueError, RuntimeError) as exc:
            print(f"event stream disconnected: {type(exc).__name__}: {exc}; retrying", flush=True)
            time.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    main()
