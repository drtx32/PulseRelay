"""The single outbound transport contract for PulseRelay."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from core.event import EventEnvelope
from core.persistence import DestinationRecord


@dataclass
class WebhookResult:
    status: str
    http_status: int | None = None
    external_id: str = ""
    response_excerpt: str = ""
    error: str = ""
    emitted_event: dict[str, Any] | None = None
    retry_after: float | None = None
    request_excerpt: str = ""


def stable_idempotency_key(subject_id: str, destination_id: str, payload_version: str = "v1") -> str:
    return f"{subject_id}:{destination_id}:{payload_version}"


def _resolve(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return value


def _private_host(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    if not host: return True
    try: return ipaddress.ip_address(socket.gethostbyname(host)).is_private
    except OSError: return True


def _template_content(subject: dict[str, Any]) -> Any:
    content = subject.get("content") or {}
    return content.get("text", content) if isinstance(content, dict) else content


def _replace_input_content(template: str, content: Any) -> str:
    """Replace the documented token without corrupting surrounding JSON.

    The UI presents the token inside a JSON string most of the time. Replace
    the quoted token as a complete JSON value first; the second replacement
    keeps unquoted/raw templates working as well.
    """
    encoded = json.dumps(content, ensure_ascii=False)
    return template.replace('"${input content}"', encoded).replace("'${input content}'", encoded).replace("${input content}", encoded)


class WebhookDelivery:
    def __init__(self, destination: DestinationRecord, allow_private: bool = False):
        self.destination = destination
        self.allow_private = allow_private

    async def send(self, subject: dict[str, Any], subject_id: str, delivery_id: str) -> WebhookResult:
        if not self.destination.enabled: return WebhookResult("skipped")
        url = _resolve(self.destination.url)
        if not url.startswith(("http://", "https://")) or (not self.allow_private and _private_host(url)):
            return WebhookResult("failed", error="destination URL rejected")
        config = self.destination.config or {}
        headers = {str(k): str(_resolve(v)) for k, v in (config.get("headers") or {}).items()}
        message_type = str(config.get("message_type", "json")).lower()
        template = str(config.get("message_template", ""))
        if message_type == "text":
            if template:
                content = _template_content(subject)
                if "${input content}" in template:
                    candidate = _replace_input_content(template, content)
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        body_value = template.replace("${input content}", str(content))
                        content_type = "text/plain; charset=utf-8"
                    else:
                        # A text-labelled webhook can still target a provider
                        # such as WeCom whose body is a JSON envelope.
                        body = candidate.encode("utf-8")
                        content_type = "application/json"
                        candidate = None
                        body_value = None
                else:
                    environment = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, cache_size=0)
                    environment.globals.clear(); environment.filters.clear(); environment.tests.clear()
                    body_value = environment.from_string(template).render(event=subject, input=subject.get("content", {}))
                    content_type = "text/plain; charset=utf-8"
                if body_value is not None:
                    body = body_value.encode("utf-8")
            else:
                body_value = str((subject.get("content") or {}).get("text", ""))
                body = body_value.encode("utf-8")
                content_type = "text/plain; charset=utf-8"
        else:
            if template:
                body = _replace_input_content(template, _template_content(subject)).encode("utf-8")
            else:
                body = json.dumps(subject, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        headers.update({"Content-Type": content_type, "X-PulseRelay-Event-ID": subject_id,
                        "X-PulseRelay-Delivery-ID": delivery_id, "X-PulseRelay-Timestamp": str(int(time.time())),
                        "Idempotency-Key": stable_idempotency_key(subject_id, self.destination.id, str(config.get("payload_version", "v1")))})
        signing_secret = _resolve(config.get("hmac_secret"))
        if signing_secret:
            headers["X-PulseRelay-Signature"] = hmac.new(str(signing_secret).encode(), body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(url, data=body, headers=headers, method=self.destination.method)
        timeout = float(config.get("timeout_seconds", 30))
        try:
            response = await asyncio.to_thread(urllib.request.urlopen, request, timeout=timeout)
            text = response.read(2000).decode("utf-8", errors="replace")
            emitted = None
            try:
                parsed = json.loads(text) if text else {}
                emitted = parsed.get("emit") if isinstance(parsed, dict) else None
            except json.JSONDecodeError: pass
            return WebhookResult("success" if response.status in set(config.get("success_statuses", [200, 201, 202, 204])) else "failed", response.status, response_excerpt=text, emitted_event=emitted, request_excerpt=body.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            text = exc.read(2000).decode("utf-8", errors="replace")
            retry_after = None
            try: retry_after = float(exc.headers.get("Retry-After"))
            except (TypeError, ValueError): pass
            return WebhookResult("failed", exc.code, response_excerpt=text, error=f"HTTP {exc.code}", retry_after=retry_after, request_excerpt=body.decode("utf-8", errors="replace"))
        except Exception as exc:
            return WebhookResult("failed", error=str(exc), request_excerpt=body.decode("utf-8", errors="replace"))
