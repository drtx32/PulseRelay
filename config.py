"""
JSON configuration loader for PulseRelay.

The shape intentionally mirrors OpenClaw-style single-file JSON configs: a top-level
config_version plus named sections for server, agent, handlers, aggregation, and
sources. Environment variables are still accepted as compatibility fallbacks so
existing deployments do not break immediately.
"""

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).parent / "data" / "pulserelay.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "server": {
        "title": "PulseRelay Gateway",
        "host": "0.0.0.0",
        "port": 8000,
    },
    "openclaw": {
        "ws_host": "127.0.0.1",
        "ws_port": 18800,
        "ws_path": "/ws",
        "sender_id": "test_user_001",
        "sender_name": "TestUser",
        "ws_token": "",
    },
    "handlers": {
        "bark": {
            "device_key": "",
        }
    },
    "aggregation": {
        "content_threshold": 1000,
        "message_threshold": 10,
        "idle_timeout": 20.0,
        "min_trigger_interval": 5.0,
        "monitor_chats": [],
    },
    "templates": {
        "message": "templates/message_summary_example.j2",
    },
    "sources": {
        "weflow": {
            "enabled": True,
            "host": "localhost",
            "port": 5031,
            "access_token": "",
        },
        "lark": {
            "enabled": False,
            "callback_path": "/sources/lark/events",
            "verification_token": "",
            "encrypt_key": "",
            "include_non_text": True,
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(data: dict[str, Any], path: str, value: Any):
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _cast_like(value: str, current: Any) -> Any:
    if isinstance(current, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, list):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _apply_env_compat(config: dict[str, Any]):
    env_map = {
        "WS_HOST": "openclaw.ws_host",
        "WS_PORT": "openclaw.ws_port",
        "WS_PATH": "openclaw.ws_path",
        "SENDER_ID": "openclaw.sender_id",
        "SENDER_NAME": "openclaw.sender_name",
        "WS_TOKEN": "openclaw.ws_token",
        "BARK_DEVICE_KEY": "handlers.bark.device_key",
        "WEFLOW_HOST": "sources.weflow.host",
        "WEFLOW_PORT": "sources.weflow.port",
        "WEFLOW_TOKEN": "sources.weflow.access_token",
        "MONITOR_CHATS": "aggregation.monitor_chats",
        "WX_MONITOR_CHATS": "aggregation.monitor_chats",
        "CONTENT_THRESHOLD": "aggregation.content_threshold",
        "WX_CONTENT_THRESHOLD": "aggregation.content_threshold",
        "MESSAGE_THRESHOLD": "aggregation.message_threshold",
        "WX_MESSAGE_THRESHOLD": "aggregation.message_threshold",
        "IDLE_TIMEOUT": "aggregation.idle_timeout",
        "WX_IDLE_TIMEOUT": "aggregation.idle_timeout",
        "MIN_TRIGGER_INTERVAL": "aggregation.min_trigger_interval",
        "MESSAGE_TEMPLATE": "templates.message",
        "WX_TEMPLATE": "templates.message",
        "LARK_ENABLED": "sources.lark.enabled",
        "LARK_VERIFICATION_TOKEN": "sources.lark.verification_token",
        "LARK_ENCRYPT_KEY": "sources.lark.encrypt_key",
        "LARK_CALLBACK_PATH": "sources.lark.callback_path",
    }

    applied: set[str] = set()
    for env_name, path in env_map.items():
        if env_name not in os.environ or path in applied:
            continue
        current = _get_path(config, path)
        _set_path(config, path, _cast_like(os.environ[env_name], current))
        applied.add(path)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or os.getenv("PULSERELAY_CONFIG", DEFAULT_CONFIG_PATH))
    config = deepcopy(DEFAULT_CONFIG)

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        config = _deep_merge(config, loaded)

    _apply_env_compat(config)
    config["_config_path"] = str(config_path)
    return config
